import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, String, Date, DateTime, Enum, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.assessment import RecordStatus  # 共用 草稿/待審/已核閱


def gen_uuid():
    return str(uuid.uuid4())


class ContactType(str, enum.Enum):
    case_person = "個案"
    family_contact = "家屬"
    caregiver = "居服員"
    organization = "A單位"
    other = "其他"
    institution = "機構端"
    family = "家屬端"
    case_self = "個案本人"


class PhoneCallType(str, enum.Enum):
    case_service_change = "個案：服務調整異動/請假"
    case_service_quantity_change = "個案：增加/縮減服務"
    case_care = "個案：個案關懷"
    case_feedback = "個案：意見反應"
    case_complaint = "個案：服務申訴"
    case_other = "個案：其他"
    caregiver_service_change = "居服員：服務調整異動/請假"
    caregiver_incident = "居服員：意外事件"
    caregiver_emergency = "居服員：緊急事件"
    caregiver_other = "居服員：其他"
    organization_matching = "A單位：服務人力媒合"
    organization_case_discussion = "A單位：個案服務討論"
    organization_other = "A單位：其他"
    routine_check = "例行關懷"
    schedule_confirm = "服務異動確認"
    service_need = "服務需求評估"
    care_communication = "照顧溝通"
    inquiry_reply = "諮詢回覆"
    feedback_followup = "意見反應追蹤"
    complaint_followup = "申訴關懷追蹤"
    other = "其他"


class TopicCategory(str, enum.Enum):
    schedule_change = "調班"
    service_change = "服務異動調整"
    service_increase = "增加服務需求"
    cognitive_gap = "認知落差溝通"
    inquiry = "諮詢"
    feedback = "意見反應"
    complaint = "申訴處理"
    other = "其他"


class RecordOrigin(str, enum.Enum):
    system_generated = "系統自動產生"
    manually_written = "居督自行編寫"


class ContactRecord(Base):
    """6.4 電訪紀錄：每日LINE對話批次分析後，依主題分類建立，
    不寫入第三章評估表單之文字備註欄位（見6.9）。"""

    __tablename__ = "contact_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    line_group_id = Column(String, ForeignKey("line_groups.id"), nullable=True)

    contact_type = Column(Enum(ContactType))
    phone_call_type = Column(Enum(PhoneCallType))
    contact_detail = Column(String)  # 聯絡對象細節，或「待居督確認」

    contact_date = Column(Date, nullable=False)
    topic_category = Column(Enum(TopicCategory), nullable=False)
    summary = Column(String)
    raw_log_link = Column(String)  # 原始對話紀錄連結，避免摘要失真時無從查證
    followup_required = Column(Boolean, default=False)
    followup_note = Column(String)
    followup_completed_at = Column(DateTime)
    followup_completed_by = Column(String, ForeignKey("users.id"))

    origin = Column(Enum(RecordOrigin), nullable=False, default=RecordOrigin.system_generated)
    status = Column(Enum(RecordStatus), nullable=False, default=RecordStatus.draft)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", foreign_keys=[case_id])

    @property
    def contact_type_label(self) -> str:
        if not self.contact_type:
            return "-"
        if self.contact_type in {ContactType.case_person, ContactType.case_self, ContactType.institution}:
            return "個案"
        if self.contact_type in {ContactType.family_contact, ContactType.family}:
            return "家屬"
        if self.contact_type == ContactType.caregiver:
            return "居服員"
        if self.contact_type == ContactType.organization:
            return "A單位"
        if self.contact_type == ContactType.other:
            return "其他"
        return self.contact_type.value

    @property
    def phone_call_type_label(self) -> str:
        if not self.phone_call_type:
            return self.topic_category.value if self.topic_category else "-"
        label = self.phone_call_type.value if isinstance(self.phone_call_type, PhoneCallType) else str(self.phone_call_type)
        return label.split("：", 1)[1] if "：" in label else label

    @property
    def contact_detail_label(self) -> str:
        def clean_label(value: str) -> str:
            return value.strip().lstrip("@＠!！*＊").strip()

        technical_labels = ("LINE 群組對話分析帶入", "待居督確認")
        if self.contact_detail and not any(label in self.contact_detail for label in technical_labels):
            return clean_label(self.contact_detail)
        if self.summary:
            for line in self.summary.splitlines():
                text = line.strip()
                if text.startswith("相關人員："):
                    return clean_label(text.split("：", 1)[1]) or self.contact_type_label
                if "：" in text and not text.startswith(("主題：", "過程：", "處理結果：", "後續追蹤：")):
                    return clean_label(text.split("：", 1)[0]) or self.contact_type_label
        return self.contact_type_label
