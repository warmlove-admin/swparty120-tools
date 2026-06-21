import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Enum, ForeignKey

from app.database import Base
from app.models.assessment import RecordStatus  # 共用 草稿/待審/已核閱


def gen_uuid():
    return str(uuid.uuid4())


class ContactType(str, enum.Enum):
    institution = "機構端"
    family = "家屬端"
    case_self = "個案本人"


class TopicCategory(str, enum.Enum):
    schedule_change = "調班"
    service_change = "服務項目異動"
    cognitive_gap = "認知落差溝通"
    inquiry = "諮詢"
    feedback = "意見反應"
    complaint = "申訴"
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
    contact_detail = Column(String)  # 聯絡對象細節，或「待居督確認」

    contact_date = Column(Date, nullable=False)
    topic_category = Column(Enum(TopicCategory), nullable=False)
    summary = Column(String)
    raw_log_link = Column(String)  # 原始對話紀錄連結，避免摘要失真時無從查證

    origin = Column(Enum(RecordOrigin), nullable=False, default=RecordOrigin.system_generated)
    status = Column(Enum(RecordStatus), nullable=False, default=RecordStatus.draft)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
