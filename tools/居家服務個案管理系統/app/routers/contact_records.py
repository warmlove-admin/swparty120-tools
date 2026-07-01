import base64
import hashlib
import hmac
import json
import calendar
from pathlib import Path
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.config import settings
from app.database import get_db
from app.models.case import Case
from app.models.assessment import RecordStatus
from app.models.contact_record import ContactRecord, ContactType, PhoneCallType, RecordOrigin, TopicCategory
from app.models.complaint import Complaint, ComplaintProgressEntry
from app.models.line_group import LineGroup
from app.models.line_daily_analysis import LineDailyAnalysis
from app.models.line_message import LineMessage, LineMessageSource
from app.models.line_source_link import LineSourceCategory, LineSourceKind, LineSourceLink
from app.models.record_status_log import RecordStatusLog
from app.models.user import User, UserRole
from app.routers.cases import visible_cases_query
from app.services.contact_review import (
    required_final_reviewer_label,
)
from app.services.line_daily_analysis import run_daily_line_analysis
from app.services.line_analysis import (
    _clean_contact_name,
    _format_contact_summary,
    analyze_line_conversation,
    create_contact_record_from_analysis,
    store_pasted_line_messages,
)
from app.services.line_profile import fetch_line_group_member_display_name, fetch_line_source_display_name
from app.services.signature_stamps import contact_review_rows
from app.services.record_workflow import (
    approval_status_after_role,
    can_user_approve,
    can_user_return,
    final_reviewer_role,
    next_reviewer_role,
    workflow_status_label,
)
from app.services.line_source_matcher import (
    apply_auto_source_match,
    candidate_caregivers_for_source,
    candidate_cases_for_source,
    get_or_create_line_source,
    infer_category,
    source_kind_from_line_type,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


IGNORED_LINE_MESSAGE_TYPES = {"sticker", "image"}

# 來自這些群組的訊息不建立來源、不寫入資料庫
IGNORED_LINE_SOURCE_IDS = {"U163f222a603b102c34be2dc5c9f65f6a"}


def _line_case_sort_key(case: Case) -> tuple[int, int, str]:
    org_case_no = case.org_case_no or ""
    if org_case_no.startswith("XLSROW"):
        return (1, 0, case.name)
    if org_case_no.isdigit():
        return (0, -int(org_case_no), case.name)
    return (1, 0, case.name)


CONTACT_TYPE_OPTIONS = [
    {"value": ContactType.case_person.value, "label": "個案"},
    {"value": ContactType.family_contact.value, "label": "家屬"},
    {"value": ContactType.caregiver.value, "label": "居服員"},
    {"value": ContactType.organization.value, "label": "A單位"},
    {"value": ContactType.other.value, "label": "其他"},
]

FAMILY_RELATION_OPTIONS = ["配偶", "子女", "父母", "兄弟姊妹", "媳婦／女婿", "主要照顧者", "其他家屬"]

CASE_PHONE_CALL_TYPES = [
    PhoneCallType.case_service_change,
    PhoneCallType.case_service_quantity_change,
    PhoneCallType.case_care,
    PhoneCallType.case_feedback,
    PhoneCallType.case_complaint,
    PhoneCallType.case_other,
]


PHONE_CALL_TYPE_OPTIONS = {
    ContactType.case_person.value: CASE_PHONE_CALL_TYPES,
    ContactType.family_contact.value: CASE_PHONE_CALL_TYPES,
    ContactType.caregiver.value: [
        PhoneCallType.caregiver_service_change,
        PhoneCallType.caregiver_incident,
        PhoneCallType.caregiver_emergency,
        PhoneCallType.caregiver_other,
    ],
    ContactType.organization.value: [
        PhoneCallType.organization_matching,
        PhoneCallType.organization_case_discussion,
        PhoneCallType.organization_other,
    ],
    ContactType.other.value: [PhoneCallType.case_other],
}


def _strip_contact_marker(value: str | None) -> str:
    return (value or "").strip().lstrip("@＠!！*＊").strip()


def _contact_type_from_line_source(source: LineSourceLink | None) -> ContactType | None:
    if not source:
        return None
    if source.category == LineSourceCategory.caregiver:
        return ContactType.caregiver
    if source.category == LineSourceCategory.organization:
        return ContactType.organization
    if source.category == LineSourceCategory.case_family:
        return ContactType.case_person if source.display_name and "本人" in source.display_name else ContactType.family_contact
    return None


def _line_source_has_identity(source: LineSourceLink | None) -> bool:
    if not source:
        return False
    if source.case_id or source.caregiver_user_id:
        return True
    if source.category in {LineSourceCategory.caregiver, LineSourceCategory.organization}:
        return True
    if source.category == LineSourceCategory.case_family and (source.display_name or source.relation_label):
        return True
    return False


def _line_source_can_create_contact_draft(source: LineSourceLink | None) -> bool:
    return bool(source and source.category == LineSourceCategory.case_family and source.case_id)


def _is_ignored_line_message_type(message_type: str | None) -> bool:
    return (message_type or "").strip().lower() in IGNORED_LINE_MESSAGE_TYPES


def _cleanup_ignored_line_messages(db: Session) -> int:
    deleted_messages = (
        db.query(LineMessage)
        .filter(LineMessage.message_type.in_(IGNORED_LINE_MESSAGE_TYPES))
        .delete(synchronize_session=False)
    )
    if IGNORED_LINE_SOURCE_IDS:
        deleted_messages += (
            db.query(LineMessage)
            .filter(
                LineMessage.line_platform_group_id.in_(IGNORED_LINE_SOURCE_IDS)
                | LineMessage.line_user_id.in_(IGNORED_LINE_SOURCE_IDS)
            )
            .delete(synchronize_session=False)
        )
    orphan_sources = db.query(LineSourceLink).all()
    for source in orphan_sources:
        if _line_source_has_identity(source):
            continue
        has_messages = db.query(LineMessage.id).filter(
            (LineMessage.line_platform_group_id == source.source_id)
            | (LineMessage.line_user_id == source.source_id)
        ).first()
        if not has_messages:
            db.delete(source)
    return deleted_messages


def _sync_line_messages_for_source(db: Session, source: LineSourceLink) -> None:
    message_case_id = source.case_id if _line_source_can_create_contact_draft(source) else None
    db.query(LineMessage).filter(
        (LineMessage.line_platform_group_id == source.source_id)
        | (LineMessage.line_user_id == source.source_id)
    ).update({LineMessage.case_id: message_case_id, LineMessage.sender_name: source.display_name}, synchronize_session=False)


def _refresh_line_source_display_name(db: Session, source: LineSourceLink) -> bool:
    if source.display_name:
        return False
    display_name = fetch_line_source_display_name(source.source_kind, source.source_id)
    if not display_name:
        return False
    source.display_name = display_name
    source.category = infer_category(display_name, source.source_kind)
    changed = apply_auto_source_match(db, source)
    _sync_line_messages_for_source(db, source)
    return changed or True


def _message_display_text(message: LineMessage | None) -> str:
    if not message:
        return ""
    if message.message_text:
        return message.message_text
    return f"[{message.message_type or '非文字訊息'}]"


def _source_card_title(source: LineSourceLink) -> str:
    if source.display_name:
        return source.display_name
    if source.category == LineSourceCategory.case_family:
        return "未命名個案／家屬來源"
    if source.category == LineSourceCategory.caregiver:
        return "未命名居服員來源"
    if source.category == LineSourceCategory.organization:
        return "未命名 A 單位來源"
    return "未命名 LINE 來源"


def _line_analysis_actor(db: Session, case: Case) -> User | None:
    if case.primary_supervisor_id:
        user = db.query(User).filter(User.id == case.primary_supervisor_id).first()
        if user:
            return user
    return (
        db.query(User)
        .filter(User.role.in_([UserRole.supervisor, UserRole.manager, UserRole.director]))
        .order_by(User.role.asc(), User.display_name.asc())
        .first()
    ) or db.query(User).first()


def _analyze_source_messages_for_date(db: Session, source: LineSourceLink, user: User, target_date: date) -> int:
    if not _line_source_can_create_contact_draft(source):
        return 0
    existing = db.query(LineDailyAnalysis).filter(
        LineDailyAnalysis.case_id == source.case_id,
        LineDailyAnalysis.analysis_date == target_date,
        LineDailyAnalysis.line_platform_group_id == source.source_id,
    ).order_by(LineDailyAnalysis.created_at.desc()).first()
    start_at = datetime.combine(target_date, datetime.min.time())
    end_at = datetime.combine(target_date, datetime.max.time())
    messages = (
        db.query(LineMessage)
        .filter(
            ((LineMessage.line_platform_group_id == source.source_id) | (LineMessage.line_user_id == source.source_id)),
            LineMessage.case_id == source.case_id,
            LineMessage.message_type.notin_(IGNORED_LINE_MESSAGE_TYPES),
            LineMessage.event_timestamp >= start_at,
            LineMessage.event_timestamp <= end_at,
        )
        .order_by(LineMessage.event_timestamp.asc(), LineMessage.created_at.asc())
        .all()
    )
    if not messages:
        return 0
    case = db.query(Case).filter(Case.id == source.case_id).first()
    if not case:
        return 0
    raw_text = "\n".join(
        f"{message.sender_name or source.display_name or message.line_user_id or 'LINE'}：{_message_display_text(message)}"
        for message in messages
    )
    analysis = analyze_line_conversation(raw_text)
    if existing and existing.contact_record_id:
        contact_record = db.query(ContactRecord).filter(ContactRecord.id == existing.contact_record_id).first()
        if contact_record and contact_record.status == RecordStatus.draft:
            contact_record.contact_type = _contact_type_from_line_source(source) or analysis.contact_type
            contact_record.phone_call_type = analysis.phone_call_type
            contact_record.contact_detail = _clean_contact_name(analysis.related_people)
            contact_record.topic_category = analysis.topic_category
            contact_record.summary = _format_contact_summary(analysis)
            contact_record.followup_required = analysis.followup_required
            contact_record.followup_note = analysis.followup_note
            contact_record.updated_at = datetime.utcnow()
            existing.message_count = len(messages)
            existing.note = f"已對應來源自動更新，由 {len(messages)} 則 LINE 訊息彙整。"
            return 2
        if contact_record:
            return 0
        existing.contact_record_id = None
        existing.complaint_id = None
    contact_record, complaint = create_contact_record_from_analysis(
        db=db,
        case=case,
        user=user,
        raw_text=raw_text,
        contact_date=target_date,
        contact_type=_contact_type_from_line_source(source),
    )
    if existing:
        existing.message_count = len(messages)
        existing.contact_record_id = contact_record.id
        existing.complaint_id = complaint.id if complaint else None
        existing.note = f"補查後重新建立，由 {len(messages)} 則 LINE 訊息產生。"
    else:
        db.add(LineDailyAnalysis(
            case_id=case.id,
            line_platform_group_id=source.source_id,
            analysis_date=target_date,
            message_count=len(messages),
            contact_record_id=contact_record.id,
            complaint_id=complaint.id if complaint else None,
            note=f"來源對應儲存後，由 {len(messages)} 則 LINE 訊息產生。",
        ))
    return 1


def _analyze_source_messages_for_today(db: Session, source: LineSourceLink, user: User) -> int:
    return _analyze_source_messages_for_date(db, source, user, date.today())


def sync_line_sources_from_messages(db: Session) -> None:
    """把舊版已收到的 LINE 訊息補成來源清單。"""
    rows = (
        db.query(LineMessage.line_platform_group_id, LineMessage.line_user_id, LineMessage.sender_name)
        .filter((LineMessage.line_platform_group_id.isnot(None)) | (LineMessage.line_user_id.isnot(None)))
        .all()
    )
    for platform_group_id, user_id, sender_name in rows:
        if platform_group_id:
            source = get_or_create_line_source(db, LineSourceKind.group, platform_group_id)
            if _refresh_line_source_display_name(db, source) or apply_auto_source_match(db, source):
                _sync_line_messages_for_source(db, source)
        if user_id:
            source = get_or_create_line_source(db, LineSourceKind.user, user_id, sender_name)
            if _refresh_line_source_display_name(db, source) or apply_auto_source_match(db, source):
                _sync_line_messages_for_source(db, source)
    db.flush()


def _current_webhook_url(request: Request) -> str:
    ngrok_api_path = Path("data/ngrok-api.json")
    if ngrok_api_path.exists():
        try:
            payload = json.loads(ngrok_api_path.read_text(encoding="utf-8-sig"))
            public_url = next(
                (item.get("public_url") for item in payload.get("tunnels", []) if item.get("proto") == "https"),
                None,
            )
            if public_url:
                return f"{public_url}/line/webhook"
        except (OSError, json.JSONDecodeError):
            pass
    return f"{request.url.scheme}://{request.url.netloc}/line/webhook"


def _contact_record_or_redirect(db: Session, user: User, record_id: str) -> tuple[ContactRecord | None, Case | None]:
    record = db.query(ContactRecord).filter(ContactRecord.id == record_id).first()
    if not record:
        return None, None
    case = visible_cases_query(db, user).filter(Case.id == record.case_id).first()
    if not case:
        return None, None
    return record, case


def _can_supervisor_sign_contact(user: User, case: Case) -> bool:
    return user.role in {UserRole.manager, UserRole.director} or case.primary_supervisor_id == user.id


def _write_contact_status_log(
    db: Session,
    record: ContactRecord,
    user: User,
    to_status: RecordStatus,
    change_note: str | None = None,
):
    db.add(RecordStatusLog(
        record_type="contact_record",
        record_id=record.id,
        from_status=record.status.value if record.status else None,
        to_status=to_status.value,
        changed_by=user.id,
        change_note=change_note.strip() if change_note and change_note.strip() else None,
    ))
    record.status = to_status


def _contact_review_logs(db: Session, record_id: str) -> list[RecordStatusLog]:
    return db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "contact_record",
        RecordStatusLog.record_id == record_id,
    ).order_by(RecordStatusLog.created_at.asc()).all()


def _phone_call_type_options_for_template() -> dict[str, list[PhoneCallType]]:
    return PHONE_CALL_TYPE_OPTIONS


def _topic_category_from_phone_call_type(phone_call_type: PhoneCallType) -> TopicCategory:
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
    return TopicCategory.other


def _contact_detail_from_form(contact_type: ContactType, contact_detail: str, family_relation: str, other_contact_detail: str) -> str:
    if contact_type == ContactType.family_contact:
        if family_relation not in FAMILY_RELATION_OPTIONS:
            raise HTTPException(400, "選擇家屬時必須選擇關係")
        return f"家屬（{family_relation}）"
    if contact_type == ContactType.other:
        other_detail = _strip_contact_marker(other_contact_detail)
        if not other_detail:
            raise HTTPException(400, "選擇其他時必須手動輸入聯絡對象")
        return other_detail
    return contact_type.value


@router.get("/cases/{case_id}/contact-records/line-analysis/new", response_class=HTMLResponse)
def line_analysis_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    return templates.TemplateResponse(
        request,
        "line_analysis_form.html",
        {
            "case": case,
            "user": user,
            "today": date.today(),
            "contact_type_options": CONTACT_TYPE_OPTIONS,
            "family_relation_options": FAMILY_RELATION_OPTIONS,
            "phone_call_type_options": PHONE_CALL_TYPE_OPTIONS,
            "error": None,
        },
    )


@router.get("/cases/{case_id}/contact-records/new", response_class=HTMLResponse)
def manual_contact_record_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以新增此個案的電訪紀錄")
    return templates.TemplateResponse(
        request,
        "contact_record_manual_form.html",
        {
            "case": case,
            "user": user,
            "today": date.today(),
            "contact_type_options": CONTACT_TYPE_OPTIONS,
            "family_relation_options": FAMILY_RELATION_OPTIONS,
            "phone_call_type_options": _phone_call_type_options_for_template(),
        },
    )


@router.post("/cases/{case_id}/contact-records")
def create_manual_contact_record(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    contact_date: str = Form(...),
    contact_type: str = Form(...),
    phone_call_type: str = Form(...),
    contact_detail: str = Form(""),
    family_relation: str = Form(""),
    other_contact_detail: str = Form(""),
    summary: str = Form(...),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以新增此個案的電訪紀錄")
    selected_contact_type = ContactType(contact_type)
    selected_phone_call_type = PhoneCallType(phone_call_type)
    clean_summary = summary.strip()
    if not clean_summary:
        raise HTTPException(400, "電訪紀錄內容不可空白")
    record = ContactRecord(
        case_id=case.id,
        contact_type=selected_contact_type,
        phone_call_type=selected_phone_call_type,
        contact_detail=_contact_detail_from_form(selected_contact_type, contact_detail, family_relation, other_contact_detail),
        contact_date=date.fromisoformat(contact_date),
        topic_category=_topic_category_from_phone_call_type(selected_phone_call_type),
        summary=clean_summary,
        raw_log_link="居督手動新增",
        origin=RecordOrigin.manually_written,
        status=RecordStatus.draft,
    )
    db.add(record)
    db.commit()
    return RedirectResponse(url=f"/contact-records/{record.id}?saved=1", status_code=302)


@router.post("/cases/{case_id}/contact-records/line-analysis")
def create_line_analysis_record(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    contact_date: str = Form(...),
    contact_type: str = Form(""),
    phone_call_type: str = Form(""),
    raw_text: str = Form(...),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    if not raw_text.strip():
        return templates.TemplateResponse(
            request,
            "line_analysis_form.html",
            {
                "case": case,
                "user": user,
                "today": date.today(),
                "contact_type_options": CONTACT_TYPE_OPTIONS,
                "family_relation_options": FAMILY_RELATION_OPTIONS,
                "phone_call_type_options": PHONE_CALL_TYPE_OPTIONS,
                "error": "請貼上 LINE 群組對話內容。",
            },
            status_code=400,
        )
    parsed_date = date.fromisoformat(contact_date)
    selected_phone_call_type = PhoneCallType(phone_call_type) if phone_call_type else None
    selected_contact_type = ContactType(contact_type) if contact_type else None
    store_pasted_line_messages(db, case, raw_text, parsed_date)
    create_contact_record_from_analysis(db, case, user, raw_text, parsed_date, selected_phone_call_type, selected_contact_type)
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}?tab=contact", status_code=302)


@router.post("/line/daily-analysis/run")
def run_line_daily_analysis_now(
    analysis_date: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    target_date = date.fromisoformat(analysis_date)
    result = run_daily_line_analysis(db, target_date, user)
    db.commit()
    return RedirectResponse(
        url=(
            "/line/daily-analyses"
            f"?analysis_date={target_date.isoformat()}"
            f"&created={result.created}"
            f"&skipped={result.skipped}"
            f"&message_groups={result.message_groups}"
        ),
        status_code=302,
    )


@router.post("/line/webhook")
async def line_webhook(request: Request, db: Session = Depends(get_db)):
    """接收 LINE webhook 訊息。

    正式串接時須設定 LINE_CHANNEL_SECRET；本機未設定時允許測試工具送入假資料。
    """
    body = await request.body()
    if settings.line_channel_secret:
        signature = request.headers.get("x-line-signature")
        digest = hmac.new(settings.line_channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
        expected_signature = base64.b64encode(digest).decode("utf-8")
        if not signature or not hmac.compare_digest(signature, expected_signature):
            raise HTTPException(status_code=400, detail="LINE webhook signature verification failed")
    payload = json.loads(body.decode("utf-8")) if body else {}
    sources_to_analyze: dict[str, LineSourceLink] = {}
    for event in payload.get("events", []):
        source = event.get("source", {})
        message = event.get("message", {})
        message_type = message.get("type") or event.get("type") or "unknown"
        if _is_ignored_line_message_type(message_type):
            continue
        group_id = source.get("groupId") or source.get("roomId")
        if group_id and group_id in IGNORED_LINE_SOURCE_IDS:
            continue
        user_id = source.get("userId")
        if user_id and user_id in IGNORED_LINE_SOURCE_IDS:
            continue
        source_type = source.get("type")
        source_id = source.get("groupId") or source.get("roomId") or source.get("userId")
        source_kind = source_kind_from_line_type(source_type)
        source_link = None
        if source_id:
            source_name = (
                source.get("displayName")
                or source.get("name")
                or fetch_line_source_display_name(source_kind, source_id)
            )
            source_link = get_or_create_line_source(db, source_kind, source_id, source_name)
            if _refresh_line_source_display_name(db, source_link) or apply_auto_source_match(db, source_link):
                _sync_line_messages_for_source(db, source_link)
        sender_name = source_link.display_name if source_link else None
        if group_id and source.get("userId"):
            sender_name = fetch_line_group_member_display_name(group_id, source.get("userId")) or sender_name
        line_group = None
        case = None
        if _line_source_can_create_contact_draft(source_link):
            case = db.query(Case).filter(Case.id == source_link.case_id).first()
        elif group_id:
            line_group = db.query(LineGroup).filter(LineGroup.line_group_id == group_id).first()
            if line_group and line_group.case_id:
                case = db.query(Case).filter(Case.id == line_group.case_id).first()
            if not case:
                case = db.query(Case).filter(Case.line_group_id == group_id).first()
        timestamp = datetime.fromtimestamp((event.get("timestamp") or 0) / 1000) if event.get("timestamp") else datetime.utcnow()
        db.add(LineMessage(
            case_id=case.id if case else None,
            line_group_id=line_group.id if line_group else None,
            line_platform_group_id=group_id,
            line_user_id=source.get("userId"),
            sender_name=sender_name,
            message_type=message_type,
            message_text=message.get("text"),
            event_timestamp=timestamp,
        ))
        if _line_source_can_create_contact_draft(source_link):
            sources_to_analyze[source_link.source_id] = source_link
    db.flush()
    for source_link in sources_to_analyze.values():
        case = db.query(Case).filter(Case.id == source_link.case_id).first()
        actor = _line_analysis_actor(db, case) if case else None
        if actor:
            _analyze_source_messages_for_today(db, source_link, actor)
    db.commit()
    return {"status": "ok"}


@router.get("/contact-records/{record_id}", response_class=HTMLResponse)
def contact_record_detail(
    record_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record, case = _contact_record_or_redirect(db, user, record_id)
    if not record or not case:
        return RedirectResponse(url="/cases", status_code=302)
    review_logs = _contact_review_logs(db, record.id)
    next_reviewer = next_reviewer_role("contact_record", record, review_logs)
    return templates.TemplateResponse(
        request,
        "contact_record_detail.html",
        {
            "case": case,
            "record": record,
            "user": user,
            "review_logs": review_logs,
            "review_rows": contact_review_rows(review_logs),
            "contact_type_options": CONTACT_TYPE_OPTIONS,
            "family_relation_options": FAMILY_RELATION_OPTIONS,
            "phone_call_type_options": _phone_call_type_options_for_template(),
            "final_reviewer_label": required_final_reviewer_label(record),
            "workflow_status_label": workflow_status_label("contact_record", record, review_logs),
            "next_reviewer_label": next_reviewer.value if next_reviewer else None,
            "next_action_label": "決行" if next_reviewer and next_reviewer == final_reviewer_role("contact_record", record) else "核章",
            "can_supervisor_sign": record.status == RecordStatus.draft and _can_supervisor_sign_contact(user, case),
            "can_edit_contact": record.status == RecordStatus.draft and _can_supervisor_sign_contact(user, case),
            "can_final_approve": can_user_approve("contact_record", record, review_logs, user),
            "can_return_contact": can_user_return("contact_record", record, review_logs, user),
            "needs_final_reviewer": True,
        },
    )


@router.post("/contact-records/{record_id}/edit")
def update_contact_record_draft(
    record_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    contact_date: str = Form(...),
    contact_type: str = Form(...),
    phone_call_type: str = Form(...),
    contact_detail: str = Form(""),
    family_relation: str = Form(""),
    other_contact_detail: str = Form(""),
    summary: str = Form(...),
):
    record, case = _contact_record_or_redirect(db, user, record_id)
    if not record or not case:
        return RedirectResponse(url="/cases", status_code=302)
    if record.status != RecordStatus.draft:
        raise HTTPException(400, "只有草稿電訪紀錄可以修改")
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以修改此電訪紀錄")
    selected_contact_type = ContactType(contact_type)
    record.contact_date = date.fromisoformat(contact_date)
    record.contact_type = selected_contact_type
    record.phone_call_type = PhoneCallType(phone_call_type)
    record.contact_detail = _contact_detail_from_form(selected_contact_type, contact_detail, family_relation, other_contact_detail)
    record.summary = summary.strip()
    record.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=f"/contact-records/{record.id}?saved=1", status_code=302)


@router.post("/contact-records/{record_id}/delete")
def delete_contact_record_draft(
    record_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    record, case = _contact_record_or_redirect(db, user, record_id)
    if not record or not case:
        return RedirectResponse(url="/cases", status_code=302)
    if record.status != RecordStatus.draft:
        raise HTTPException(400, "只有草稿電訪紀錄可以刪除")
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以刪除此電訪紀錄")
    complaints = db.query(Complaint).filter(Complaint.contact_record_id == record.id).all()
    for complaint in complaints:
        db.query(ComplaintProgressEntry).filter(ComplaintProgressEntry.complaint_id == complaint.id).delete(synchronize_session=False)
        db.delete(complaint)
    db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "contact_record",
        RecordStatusLog.record_id == record.id,
    ).delete(synchronize_session=False)
    db.delete(record)
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}?tab=contact&contact_deleted=1", status_code=302)


@router.post("/cases/{case_id}/contact-records/batch-delete")
def batch_delete_contact_record_drafts(
    case_id: str,
    record_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以刪除此個案的電訪草稿")
    if not record_ids:
        return RedirectResponse(url=f"/cases/{case.id}?tab=contact&contact_deleted=0", status_code=302)
    records = db.query(ContactRecord).filter(
        ContactRecord.case_id == case.id,
        ContactRecord.id.in_(record_ids),
        ContactRecord.status == RecordStatus.draft,
    ).all()
    deleted = 0
    for record in records:
        complaints = db.query(Complaint).filter(Complaint.contact_record_id == record.id).all()
        for complaint in complaints:
            db.query(ComplaintProgressEntry).filter(ComplaintProgressEntry.complaint_id == complaint.id).delete(synchronize_session=False)
            db.delete(complaint)
        db.query(RecordStatusLog).filter(
            RecordStatusLog.record_type == "contact_record",
            RecordStatusLog.record_id == record.id,
        ).delete(synchronize_session=False)
        db.delete(record)
        deleted += 1
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}?tab=contact&contact_deleted={deleted}", status_code=302)


@router.post("/cases/{case_id}/contact-records/recheck-line")
def recheck_case_line_messages(
    case_id: str,
    analysis_date: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以補查此個案的 LINE 訊息")
    target_date = date.fromisoformat(analysis_date)
    sources = db.query(LineSourceLink).filter(LineSourceLink.case_id == case.id).all()
    created = 0
    updated = 0
    skipped = 0
    for source in sources:
        result = _analyze_source_messages_for_date(db, source, user, target_date)
        if result == 1:
            created += 1
        elif result == 2:
            updated += 1
        else:
            skipped += 1
    db.commit()
    return RedirectResponse(
        url=(
            f"/cases/{case.id}?tab=contact"
            f"&line_recheck_date={target_date.isoformat()}"
            f"&line_recheck_created={created}"
            f"&line_recheck_updated={updated}"
            f"&line_recheck_skipped={skipped}"
        ),
        status_code=302,
    )


@router.post("/contact-records/{record_id}/submit-review")
def submit_contact_record_review(
    record_id: str,
    review_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    record, case = _contact_record_or_redirect(db, user, record_id)
    if not record or not case:
        return RedirectResponse(url="/cases", status_code=302)
    if record.status != RecordStatus.draft:
        raise HTTPException(400, "只有草稿電訪紀錄可以送出核閱")
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以核閱此電訪紀錄")
    _write_contact_status_log(db, record, user, RecordStatus.pending, review_note or None)
    db.commit()
    return RedirectResponse(url=f"/contact-records/{record.id}", status_code=302)


@router.post("/contact-records/{record_id}/approve")
def approve_contact_record(
    record_id: str,
    approval_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    record, case = _contact_record_or_redirect(db, user, record_id)
    if not record or not case:
        return RedirectResponse(url="/cases", status_code=302)
    review_logs = _contact_review_logs(db, record.id)
    if record.status != RecordStatus.pending:
        raise HTTPException(400, "只有待審電訪紀錄可以核閱")
    if not can_user_approve("contact_record", record, review_logs, user):
        raise HTTPException(403, "此電訪紀錄不屬於目前角色核閱")
    next_status = approval_status_after_role("contact_record", record, user.role)
    _write_contact_status_log(db, record, user, next_status, approval_note or None)
    db.commit()
    return RedirectResponse(url=f"/contact-records/{record.id}", status_code=302)


@router.post("/contact-records/{record_id}/return")
def return_contact_record(
    record_id: str,
    return_reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    record, case = _contact_record_or_redirect(db, user, record_id)
    if not record or not case:
        return RedirectResponse(url="/cases", status_code=302)
    review_logs = _contact_review_logs(db, record.id)
    if record.status not in {RecordStatus.pending, RecordStatus.approved}:
        raise HTTPException(400, "只有待審或已決行電訪紀錄可以取消核章／退回")
    if not can_user_return("contact_record", record, review_logs, user):
        raise HTTPException(403, "此電訪紀錄不屬於目前角色取消核章／退回")
    reason = return_reason.strip()
    if not reason:
        raise HTTPException(400, "退回原因不可空白")
    _write_contact_status_log(db, record, user, RecordStatus.draft, reason)
    db.commit()
    return RedirectResponse(url=f"/contact-records/{record.id}", status_code=302)


@router.post("/contact-records/{record_id}/followup-complete")
def complete_contact_followup(
    record_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    record, case = _contact_record_or_redirect(db, user, record_id)
    if not record or not case:
        return RedirectResponse(url="/cases", status_code=302)
    if not _can_supervisor_sign_contact(user, case):
        raise HTTPException(403, "只有主責居督可以完成此追蹤提醒")
    record.followup_completed_at = datetime.utcnow()
    record.followup_completed_by = user.id
    record.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/todos#contact-followups", status_code=302)


@router.post("/contact-records/batch-approve")
def batch_approve_contact_records(
    record_ids: list[str] = Form(default=[]),
    approval_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    if not record_ids:
        return RedirectResponse(url="/todos?contact_batch=0", status_code=302)
    approved = 0
    records = db.query(ContactRecord).filter(ContactRecord.id.in_(record_ids)).all()
    for record in records:
        review_logs = _contact_review_logs(db, record.id)
        if record.status == RecordStatus.pending and can_user_approve("contact_record", record, review_logs, user):
            next_status = approval_status_after_role("contact_record", record, user.role)
            _write_contact_status_log(db, record, user, next_status, approval_note or None)
            approved += 1
    db.commit()
    return RedirectResponse(url=f"/todos?contact_batch={approved}", status_code=302)


def _parse_month_date(value: str, fallback: date) -> date:
    if not value:
        return fallback
    if len(value) == 7:
        value = f"{value}-01"
    return date.fromisoformat(value).replace(day=1)


def _month_shift(value: date, offset: int) -> date:
    month_number = value.month + offset
    return date(value.year + (month_number - 1) // 12, (month_number - 1) % 12 + 1, 1)


def _month_range(start_month: date, end_month: date) -> list[date]:
    months = []
    current = start_month
    while current <= end_month:
        months.append(current)
        current = _month_shift(current, 1)
    return months


@router.get("/cases/{case_id}/contact-records/print", response_class=HTMLResponse)
def print_contact_records(
    case_id: str,
    request: Request,
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    today = date.today()
    default_start = date(today.year, 1, 1)
    default_end = date(today.year, today.month, 1)
    start_month = _parse_month_date(start, default_start)
    end_month = _parse_month_date(end, default_end)
    if end_month < start_month:
        start_month, end_month = end_month, start_month
    range_end = _month_shift(end_month, 1)
    records = (
        db.query(ContactRecord)
        .filter(
            ContactRecord.case_id == case.id,
            ContactRecord.contact_date >= start_month,
            ContactRecord.contact_date < range_end,
        )
        .order_by(ContactRecord.contact_date.asc(), ContactRecord.created_at.asc())
        .all()
    )
    records_by_month = {month: [] for month in _month_range(start_month, end_month)}
    for record in records:
        records_by_month.setdefault(record.contact_date.replace(day=1), []).append(record)
    review_logs = (
        db.query(RecordStatusLog)
        .filter(
            RecordStatusLog.record_type == "contact_record",
            RecordStatusLog.record_id.in_([record.id for record in records]),
        )
        .order_by(RecordStatusLog.created_at.asc())
        .all()
    ) if records else []
    review_rows_by_record = {}
    for log in review_logs:
        review_rows_by_record.setdefault(log.record_id, []).append(log)
    review_rows_by_record = {
        record_id: contact_review_rows(logs)
        for record_id, logs in review_rows_by_record.items()
    }
    return templates.TemplateResponse(
        request,
        "contact_records_print.html",
        {
            "case": case,
            "user": user,
            "start_month": start_month,
            "end_month": end_month,
            "records_by_month": records_by_month,
            "review_rows_by_record": review_rows_by_record,
            "calendar": calendar,
        },
    )


@router.get("/line/daily-analyses", response_class=HTMLResponse)
def line_daily_analyses(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    analyses = (
        db.query(LineDailyAnalysis)
        .order_by(LineDailyAnalysis.analysis_date.desc(), LineDailyAnalysis.created_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "line_daily_analyses.html",
        {"user": user, "analyses": analyses, "today": date.today()},
    )


@router.get("/line/sources", response_class=HTMLResponse)
def line_sources(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    _cleanup_ignored_line_messages(db)
    sync_line_sources_from_messages(db)
    db.commit()
    sources = db.query(LineSourceLink).order_by(LineSourceLink.updated_at.desc(), LineSourceLink.created_at.desc()).all()
    recent_messages = {}
    source_message_counts = {}
    caregiver_candidates = {}
    for source in sources:
        source_query = db.query(LineMessage).filter(
            (LineMessage.line_platform_group_id == source.source_id)
            | (LineMessage.line_user_id == source.source_id),
            LineMessage.message_type.notin_(IGNORED_LINE_MESSAGE_TYPES),
        )
        source_message_counts[source.source_id] = source_query.count()
        recent_messages[source.source_id] = (
            source_query
            .order_by(LineMessage.event_timestamp.desc(), LineMessage.created_at.desc())
            .limit(5)
            .all()
        )
        caregiver_candidates[source.id] = candidate_caregivers_for_source(db, source.display_name)[:3]
    source_candidates = {
        source.id: candidate_cases_for_source(db, source.display_name, source.category)[:3]
        for source in sources
    }
    source_by_id = {source.source_id: source for source in sources}
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    today_messages = (
        db.query(LineMessage)
        .filter(
            LineMessage.event_timestamp >= today_start,
            LineMessage.event_timestamp <= today_end,
            LineMessage.message_type.notin_(IGNORED_LINE_MESSAGE_TYPES),
        )
        .order_by(LineMessage.event_timestamp.desc(), LineMessage.created_at.desc())
        .limit(50)
        .all()
    )
    today_analyses = {
        analysis.line_platform_group_id: analysis
        for analysis in db.query(LineDailyAnalysis).filter(LineDailyAnalysis.analysis_date == date.today()).all()
    }
    message_status_items = []
    for message in today_messages:
        source_identifier = message.line_platform_group_id or message.line_user_id
        source = source_by_id.get(source_identifier)
        analysis = today_analyses.get(source_identifier)
        if not _line_source_has_identity(source):
            status = "待對應"
        elif not _line_source_can_create_contact_draft(source):
            status = "已識別"
        elif analysis and analysis.contact_record_id:
            status = "已轉草稿"
        else:
            status = "待分析"
        message_status_items.append({
            "message": message,
            "source": source,
            "analysis": analysis,
            "status": status,
        })
    def source_activity_key(source: LineSourceLink) -> tuple[int, float]:
        messages = recent_messages.get(source.source_id) or []
        latest_at = messages[0].event_timestamp if messages else datetime.min
        has_today_text = any(message.event_timestamp >= today_start for message in messages)
        latest_ts = latest_at.timestamp() if latest_at != datetime.min else 0
        return (0 if has_today_text else 1, -latest_ts)

    unlinked_sources = sorted(
        [source for source in sources if not _line_source_has_identity(source)],
        key=source_activity_key,
    )
    linked_sources = sorted(
        [source for source in sources if _line_source_has_identity(source)],
        key=source_activity_key,
    )
    cases = sorted(db.query(Case).all(), key=_line_case_sort_key)
    caregivers = db.query(User).filter(User.role == UserRole.caregiver, User.is_active.is_(True)).order_by(User.display_name).all()
    return templates.TemplateResponse(
        request,
        "line_sources.html",
        {
            "user": user,
            "sources": sources,
            "unlinked_sources": unlinked_sources,
            "linked_sources": linked_sources,
            "message_status_items": message_status_items,
            "recent_messages": recent_messages,
            "source_message_counts": source_message_counts,
            "message_display_text": _message_display_text,
            "source_card_title": _source_card_title,
            "source_candidates": source_candidates,
            "caregiver_candidates": caregiver_candidates,
            "cases": cases,
            "caregivers": caregivers,
            "categories": list(LineSourceCategory),
            "contact_type_options": CONTACT_TYPE_OPTIONS,
            "phone_call_type_options": _phone_call_type_options_for_template(),
            "today": date.today(),
            "webhook_url": _current_webhook_url(request),
            "line_profile_lookup_enabled": bool(settings.line_channel_access_token),
            "saved_source_id": request.query_params.get("saved"),
            "manual_source_id": request.query_params.get("manual_source"),
            "manual_created": request.query_params.get("manual_created"),
            "analysis_created": request.query_params.get("analysis_created"),
        },
    )


@router.get("/line/messages", response_class=HTMLResponse)
def line_messages_debug(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    _cleanup_ignored_line_messages(db)
    db.commit()
    messages = db.query(LineMessage).order_by(LineMessage.event_timestamp.desc(), LineMessage.created_at.desc()).limit(100).all()
    case_ids = {message.case_id for message in messages if message.case_id}
    case_names = {
        case.id: case.name
        for case in db.query(Case).filter(Case.id.in_(case_ids)).all()
    } if case_ids else {}
    return templates.TemplateResponse(
        request,
        "line_messages.html",
        {"user": user, "messages": messages, "case_names": case_names, "message_display_text": _message_display_text},
    )


@router.post("/line/messages/delete")
def delete_line_messages(
    message_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    if message_ids:
        db.query(LineMessage).filter(LineMessage.id.in_(message_ids)).delete(synchronize_session=False)
        db.commit()
    return RedirectResponse(url=f"/line/messages?deleted={len(message_ids)}", status_code=302)


@router.post("/line/sources/{source_id}/update")
def update_line_source(
    source_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    display_name: str = Form(""),
    category: str = Form(""),
    relation_label: str = Form(""),
    case_id: str = Form(""),
    caregiver_user_id: str = Form(""),
):
    source = db.query(LineSourceLink).filter(LineSourceLink.id == source_id).first()
    if not source:
        return RedirectResponse(url="/line/sources", status_code=302)
    source.display_name = display_name.strip() or None
    source.category = LineSourceCategory(category) if category else infer_category(source.display_name, source.source_kind)
    source.relation_label = relation_label.strip() or None
    source.case_id = case_id or None
    source.caregiver_user_id = caregiver_user_id or None
    _sync_line_messages_for_source(db, source)
    created = _analyze_source_messages_for_today(db, source, user)
    db.commit()
    return RedirectResponse(url=f"/line/sources?saved={source.id}&analysis_created={created}", status_code=302)


@router.post("/line/sources/{source_id}/manual-message")
def create_manual_line_message(
    source_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    message_text: str = Form(...),
    contact_type: str = Form(""),
    phone_call_type: str = Form(""),
):
    source = db.query(LineSourceLink).filter(LineSourceLink.id == source_id).first()
    if not source:
        return RedirectResponse(url="/line/sources", status_code=302)
    text_value = message_text.strip()
    if not text_value:
        return RedirectResponse(url=f"/line/sources?manual_source={source.id}&manual_created=0", status_code=302)
    db.add(LineMessage(
        case_id=source.case_id if _line_source_can_create_contact_draft(source) else None,
        line_platform_group_id=source.source_id if source.source_kind in {LineSourceKind.group, LineSourceKind.room} else None,
        line_user_id=source.source_id if source.source_kind == LineSourceKind.user else None,
        sender_name=source.display_name,
        message_type="text",
        message_text=text_value,
        event_timestamp=datetime.utcnow(),
        source=LineMessageSource.pasted,
    ))
    contact_record = None
    if _line_source_can_create_contact_draft(source):
        case = visible_cases_query(db, user).filter(Case.id == source.case_id).first()
        if case:
            contact_record, _ = create_contact_record_from_analysis(
                db=db,
                case=case,
                user=user,
                raw_text=text_value,
                contact_date=date.today(),
                phone_call_type=PhoneCallType(phone_call_type) if phone_call_type else None,
                contact_type=ContactType(contact_type) if contact_type else _contact_type_from_line_source(source),
            )
    db.commit()
    if contact_record:
        return RedirectResponse(url=f"/contact-records/{contact_record.id}?saved=1", status_code=302)
    return RedirectResponse(url=f"/line/sources?manual_source={source.id}&manual_created=1", status_code=302)


@router.post("/line/sources/manual-message")
def create_manual_line_message_from_form(
    source_id: str = Form(...),
    message_text: str = Form(...),
    contact_type: str = Form(""),
    phone_call_type: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    return create_manual_line_message(source_id, db, user, message_text, contact_type, phone_call_type)
