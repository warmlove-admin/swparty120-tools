from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.complaint import Complaint, ComplaintProgressEntry, ComplaintStageType
from app.models.contact_record import ContactRecord, ContactType, PhoneCallType, RecordOrigin, TopicCategory
from app.models.line_message import LineMessage, LineMessageSource
from app.models.user import User


@dataclass
class LineConversationAnalysis:
    topic_category: TopicCategory
    summary: str
    handling_note: str
    contact_type: ContactType
    phone_call_type: PhoneCallType
    contact_detail: str
    related_people: str
    process_note: str
    result_note: str
    followup_required: bool
    followup_note: str | None
    should_create_complaint: bool


KEYWORDS = {
    TopicCategory.complaint: ("申訴", "投訴", "客訴", "不滿", "太誇張", "態度很差", "沒來", "遲到很久", "要檢舉", "錢不見", "東西不見", "拿走", "偷拿", "不敢再讓他服務"),
    TopicCategory.schedule_change: ("調班", "改時間", "改天", "請假", "換班", "停班", "延後", "提前", "不能來"),
    TopicCategory.service_increase: ("增加服務", "縮減服務", "減少服務", "加服務", "加時數", "增加時數", "多來", "多排", "服務需求", "新增服務", "喘息", "人力"),
    TopicCategory.service_change: ("改服務", "服務項目", "改洗澡", "改備餐", "改陪同", "暫停服務", "服務調整"),
    TopicCategory.feedback: ("反應", "建議", "希望", "覺得", "不方便", "不適合", "需要改善", "改善", "品質", "不準時", "有待改善"),
    TopicCategory.inquiry: ("請問", "詢問", "想問", "怎麼辦", "可以嗎", "費用", "補助"),
}


def _match_category(text: str) -> TopicCategory:
    normalized = text.replace(" ", "")
    if any(word in normalized for word in ("錢不見", "桌上的錢不見", "是不是居服員拿走", "不敢再讓他服務", "請幫我換人")):
        return TopicCategory.complaint
    if any(word in normalized for word in ("服務品質", "不準時", "有待改善", "品質有待改善")):
        return TopicCategory.feedback
    for category, keywords in KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return TopicCategory.other


def _infer_contact_type(text: str) -> ContactType:
    stripped = text.strip()
    if stripped.startswith("!") or any(marker in text for marker in ("\n!", "！")):
        return ContactType.caregiver
    if stripped.startswith("*") or any(marker in text for marker in ("\n*", "＊")):
        return ContactType.organization
    if stripped.startswith("@") or any(marker in text for marker in ("\n@", "＠")):
        first_line = stripped.splitlines()[0] if stripped else ""
        return ContactType.case_person if "本人" in first_line else ContactType.family_contact
    if any(word in text for word in ("居服員", "照服員", "服務員", "照顧服務員")):
        return ContactType.caregiver
    if any(word in text for word in ("A單位", "A 單位", "個管", "照專", "單位")):
        return ContactType.organization
    if any(word in text for word in ("家屬", "女兒", "兒子", "太太", "先生", "媳婦")):
        return ContactType.family_contact
    if any(word in text for word in ("個案", "本人", "阿公", "阿嬤")):
        return ContactType.case_person
    return ContactType.case_person


def _phone_call_type_for_category(category: TopicCategory, contact_type: ContactType, text: str) -> PhoneCallType:
    if contact_type == ContactType.caregiver:
        if any(word in text for word in ("跌倒", "受傷", "意外", "碰撞", "送醫")):
            return PhoneCallType.caregiver_incident
        if any(word in text for word in ("緊急", "立即", "急", "危險", "無法聯絡", "失聯")):
            return PhoneCallType.caregiver_emergency
        if category in {TopicCategory.schedule_change, TopicCategory.service_change, TopicCategory.service_increase}:
            return PhoneCallType.caregiver_service_change
        return PhoneCallType.caregiver_other
    if contact_type == ContactType.organization:
        if any(word in text for word in ("媒合", "人力", "派員", "替代", "缺工", "找人")):
            return PhoneCallType.organization_matching
        if category in {TopicCategory.schedule_change, TopicCategory.service_change, TopicCategory.service_increase, TopicCategory.inquiry}:
            return PhoneCallType.organization_case_discussion
        return PhoneCallType.organization_other
    if category in {TopicCategory.schedule_change, TopicCategory.service_change}:
        return PhoneCallType.case_service_change
    if category == TopicCategory.service_increase:
        return PhoneCallType.case_service_quantity_change
    if category == TopicCategory.feedback:
        return PhoneCallType.case_feedback
    if category == TopicCategory.complaint:
        return PhoneCallType.case_complaint
    if category in {TopicCategory.inquiry, TopicCategory.cognitive_gap}:
        return PhoneCallType.case_care
    return PhoneCallType.case_other


def _compact_lines(raw_text: str) -> list[str]:
    return [line.strip() for line in raw_text.splitlines() if line.strip()]


def _line_speaker(line: str) -> str | None:
    if "：" in line:
        prefix = line.split("：", 1)[0].strip()
        if prefix in {"餐點", "備註", "地址", "時間", "地點", "電話"}:
            return None
        return prefix
    if ":" in line and not line.lower().startswith(("http://", "https://")):
        prefix = line.split(":", 1)[0].strip()
        if len(prefix) > 20:
            return None
        return line.split(":", 1)[0].strip()
    return None


def _clean_contact_name(value: str) -> str:
    return value.strip().lstrip("@＠!！*＊").strip()


def _clean_message_text(value: str) -> str:
    text = value.strip()
    replacements = {
        "我覺得": "反映",
        "我希望": "希望",
        "你們": "本單位",
        "你們的": "本單位",
        "我們": "家屬",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.strip("。；，, ")


def _family_reply_from_messages(parts: list[tuple[str | None, str]]) -> str | None:
    family_messages = [message for speaker, message in parts if speaker == "家屬"]
    if not family_messages:
        return None
    joined = "，".join(family_messages)
    if any(word in joined for word in ("沒關係", "沒關係可", "自行處理", "自己處理")):
        return "家屬表示沒關係，可自行處理。"
    return None


def _has_staff_reply(parts: list[tuple[str | None, str]]) -> bool:
    staff_subjects = {"居督", "社工", "督導", "主管", "主任", "A單位"}
    return any(speaker in staff_subjects for speaker, _ in parts)


def _has_closing_reply(parts: list[tuple[str | None, str]]) -> bool:
    closing_keywords = ("沒關係", "自行處理", "自己處理", "了解", "可以", "接受", "謝謝", "好")
    return any(any(keyword in message for keyword in closing_keywords) for _, message in parts)


def _needs_followup(phone_call_type: PhoneCallType, parts: list[tuple[str | None, str]], raw_text: str) -> tuple[bool, str | None]:
    requires_response = {
        PhoneCallType.case_service_change: "請確認服務異動或請假是否已回覆家屬，並完成班表或服務紀錄調整。",
        PhoneCallType.case_service_quantity_change: "請確認服務日期、服務項目、人力安排及家屬回覆結果。",
        PhoneCallType.case_feedback: "請確認意見反應是否已回覆，並追蹤對方是否接受處理結果。",
        PhoneCallType.case_complaint: "請確認申訴訴求、處理回覆及後續追蹤責任。",
        PhoneCallType.organization_matching: "請確認人力媒合結果及回覆內容。",
        PhoneCallType.organization_case_discussion: "請確認服務討論結論及是否需調整照顧計畫或班表。",
    }
    if phone_call_type not in requires_response:
        return False, None
    has_staff_reply = _has_staff_reply(parts) or any(word in raw_text for word in ("已回復", "已回覆", "已告知", "已確認", "已安排", "無人力", "無法安排"))
    if not has_staff_reply:
        return True, requires_response[phone_call_type]
    if phone_call_type in {PhoneCallType.case_feedback, PhoneCallType.case_complaint} and not _has_closing_reply(parts):
        return True, requires_response[phone_call_type]
    if phone_call_type == PhoneCallType.case_service_quantity_change and any(word in raw_text for word in ("無人力", "沒人力", "無法安排")) and not _has_closing_reply(parts):
        return True, requires_response[phone_call_type]
    return False, None


def _service_quantity_subject(parts: list[tuple[str | None, str]]) -> str | None:
    for speaker, message in parts:
        if speaker in {"居督", "社工", "督導", "主管", "主任"}:
            continue
        subject = speaker or "家屬"
        text = message
        text = text.replace("請問有人力嗎？", "詢問單位人力")
        text = text.replace("請問有人力嗎", "詢問單位人力")
        text = text.replace("請問有沒有人力", "詢問單位人力")
        text = text.replace("需要喘息服務", "有喘息服務需求")
        text = text.replace("需要服務", "有服務需求")
        text = text.replace("，詢問單位人力", "，詢問單位人力")
        if "詢問單位人力" in text and "，" not in text:
            text = text.replace("詢問單位人力", "，詢問單位人力")
        text = text.strip("。；，, ")
        verb = "提出" if any(word in text for word in ("需求", "需要", "喘息", "短照", "人力", "增加", "縮減", "減少", "服務")) else "詢問"
        return f"{subject}{verb}{text}。"
    return None


def _dialogue_parts(lines: list[str]) -> list[tuple[str | None, str]]:
    parts = []
    for line in lines:
        if "：" in line:
            speaker, message = line.split("：", 1)
            if speaker.strip() in {"餐點", "備註", "地址", "時間", "地點", "電話"}:
                speaker, message = "", line
        elif ":" in line and not line.lower().startswith(("http://", "https://")) and len(line.split(":", 1)[0].strip()) <= 20:
            speaker, message = line.split(":", 1)
        else:
            speaker, message = "", line
        speaker = _speaker_subject(speaker) if speaker else None
        message = _clean_message_text(message)
        if message:
            parts.append((speaker, message))
    return parts


def _speaker_subject(raw_speaker: str) -> str:
    value = raw_speaker.strip()
    clean = _clean_contact_name(value).replace("本人", "").strip()
    if value.startswith(("@", "＠")):
        return "個案" if "本人" in value else "家屬"
    if value.startswith(("!", "！")):
        return "居服員"
    if value.startswith(("*", "＊")):
        return "A單位"
    if "本人" in value:
        return "個案"
    return clean or "家屬"


def _statement_for_part(speaker: str | None, message: str) -> str:
    if not speaker:
        return message
    if any(role in speaker for role in ("居督", "社工", "督導", "主管", "主任")):
        if message.startswith("我"):
            message = message[1:]
        return f"{speaker}回覆{message}"
    if message.startswith(("反映", "希望", "表示", "詢問", "建議")):
        return f"{speaker}{message}"
    if any(word in message for word in ("不見", "拿走", "不敢", "不準時", "不滿", "有待改善", "態度", "申訴", "投訴")):
        return f"{speaker}反映{message}"
    return f"{speaker}表示{message}"


def _related_people(lines: list[str], contact_type: ContactType) -> str:
    speakers = []
    for line in lines:
        speaker = _line_speaker(line)
        speaker = _clean_contact_name(speaker) if speaker else None
        if speaker and speaker not in speakers:
            speakers.append(speaker)
    if speakers:
        return "、".join(speakers[:5])
    return {
        ContactType.caregiver: "居服員",
        ContactType.organization: "A單位",
    }.get(contact_type, "個案或家屬")


def _professional_process(lines: list[str], category: TopicCategory) -> str:
    parts = _dialogue_parts(lines)
    if not parts:
        return "未取得可分析的文字內容。"
    if category == TopicCategory.service_increase:
        subject = _service_quantity_subject(parts)
        if subject:
            return subject
    statements = [_statement_for_part(speaker, message) for speaker, message in parts]
    if len(statements) == 1:
        return f"{statements[0]}。"
    return "\n".join(f"{index}. {statement}。" for index, statement in enumerate(statements, start=1))


def _result_note_for_type(phone_call_type: PhoneCallType, fallback: str) -> str:
    return {
        PhoneCallType.case_service_quantity_change: "後續應確認服務日期、服務項目、人力安排及回覆結果。",
        PhoneCallType.caregiver_incident: "後續應確認個案安全、家屬通知、必要通報及處置追蹤。",
        PhoneCallType.caregiver_emergency: "後續應確認即時處置、通知流程及後續追蹤責任。",
        PhoneCallType.organization_matching: "後續應確認可派人力、服務時間及回覆結果。",
        PhoneCallType.organization_case_discussion: "後續應確認討論結論、照顧計畫或班表是否需調整。",
        PhoneCallType.case_care: "後續應確認回覆內容及是否需持續追蹤。",
    }.get(phone_call_type, fallback)


def _topic_category_for_phone_call_type(phone_call_type: PhoneCallType, fallback: TopicCategory) -> TopicCategory:
    if phone_call_type in {PhoneCallType.case_feedback, PhoneCallType.feedback_followup}:
        return TopicCategory.feedback
    if phone_call_type in {PhoneCallType.case_complaint, PhoneCallType.complaint_followup}:
        return TopicCategory.complaint
    if phone_call_type in {PhoneCallType.case_service_change, PhoneCallType.caregiver_service_change}:
        return TopicCategory.schedule_change
    if phone_call_type == PhoneCallType.case_service_quantity_change:
        return TopicCategory.service_increase
    if phone_call_type in {PhoneCallType.organization_matching, PhoneCallType.organization_case_discussion}:
        return TopicCategory.inquiry
    return fallback


def analyze_line_conversation(raw_text: str) -> LineConversationAnalysis:
    lines = _compact_lines(raw_text)
    joined = "\n".join(lines)
    category = _match_category(joined)
    contact_type = _infer_contact_type(joined)
    phone_call_type = _phone_call_type_for_category(category, contact_type, joined)
    process_note = _professional_process(lines, category)

    handling_by_category = {
        TopicCategory.complaint: "後續應確認訴求、處理回覆及結案情形。",
        TopicCategory.schedule_change: "後續應完成派班確認並回覆相關人員。",
        TopicCategory.service_increase: "後續應評估需求、額度與照顧計畫調整。",
        TopicCategory.service_change: "後續應同步照顧計畫、班表及相關人員。",
        TopicCategory.feedback: "後續應記錄處理方式，並視情形追蹤改善結果。",
        TopicCategory.inquiry: "後續應完成回覆，並視內容轉介相關紀錄。",
        TopicCategory.other: "請居督確認是否需轉入其他紀錄或安排追蹤。",
    }
    parts = _dialogue_parts(lines)
    followup_required, followup_note = _needs_followup(phone_call_type, parts, joined)
    result_note = _result_note_for_type(phone_call_type, handling_by_category[category])
    family_reply = _family_reply_from_messages(parts)
    if phone_call_type == PhoneCallType.case_service_quantity_change and any(word in joined for word in ("無人力", "沒人力", "當日無法", "無法安排")):
        result_note = "已回復當日無人力"
        if family_reply:
            result_note += f"，{family_reply}"
        else:
            result_note += "，後續應視家屬回覆確認是否需持續媒合或改期安排。"

    return LineConversationAnalysis(
        topic_category=category,
        summary=process_note,
        handling_note=handling_by_category[category],
        contact_type=contact_type,
        phone_call_type=phone_call_type,
        contact_detail=f"{contact_type.value}，待居督確認實際聯絡人",
        related_people=_related_people(lines, contact_type),
        process_note=process_note,
        result_note=result_note,
        followup_required=followup_required,
        followup_note=followup_note,
        should_create_complaint=category == TopicCategory.complaint,
    )


def _format_contact_summary(analysis: LineConversationAnalysis) -> str:
    topic_prefix = "聯繫內容：\n" if "\n" in analysis.process_note else "聯繫內容："
    return (
        f"{topic_prefix}{analysis.process_note}\n"
        f"處理情形：{analysis.result_note}"
    )


def store_pasted_line_messages(
    db: Session,
    case: Case,
    raw_text: str,
    message_date: date,
) -> list[LineMessage]:
    messages = []
    for index, line in enumerate(_compact_lines(raw_text)):
        messages.append(LineMessage(
            case_id=case.id,
            line_platform_group_id=case.line_group_id,
            sender_name=None,
            message_type="text",
            message_text=line,
            event_timestamp=datetime.combine(message_date, datetime.min.time()).replace(hour=min(index, 23)),
            source=LineMessageSource.pasted,
        ))
    db.add_all(messages)
    return messages


def create_contact_record_from_analysis(
    db: Session,
    case: Case,
    user: User,
    raw_text: str,
    contact_date: date,
    phone_call_type: PhoneCallType | None = None,
    contact_type: ContactType | None = None,
) -> tuple[ContactRecord, Complaint | None]:
    analysis = analyze_line_conversation(raw_text)
    resolved_phone_call_type = phone_call_type or analysis.phone_call_type
    resolved_contact_type = contact_type or analysis.contact_type
    resolved_topic_category = _topic_category_for_phone_call_type(resolved_phone_call_type, analysis.topic_category)
    contact_record = ContactRecord(
        case_id=case.id,
        contact_type=resolved_contact_type,
        phone_call_type=resolved_phone_call_type,
        contact_detail=_clean_contact_name(analysis.related_people),
        contact_date=contact_date,
        topic_category=resolved_topic_category,
        summary=_format_contact_summary(analysis),
        raw_log_link="LINE 原始對話已留存備查",
        followup_required=analysis.followup_required,
        followup_note=analysis.followup_note,
        origin=RecordOrigin.system_generated,
    )
    db.add(contact_record)
    db.flush()

    complaint = None
    if analysis.should_create_complaint:
        responsible_id = case.primary_supervisor_id or user.id
        complaint = Complaint(
            case_id=case.id,
            contact_record_id=contact_record.id,
            summary=analysis.summary,
            received_date=contact_date,
            responsible_supervisor_id=responsible_id,
        )
        db.add(complaint)
        db.flush()
        db.add(ComplaintProgressEntry(
            complaint_id=complaint.id,
            stage_type=ComplaintStageType.initial,
            content=f"由 LINE 對話自動判定為申訴草稿。\n\n{analysis.summary}\n\n{analysis.handling_note}",
            entered_by=user.id,
        ))

    return contact_record, complaint
