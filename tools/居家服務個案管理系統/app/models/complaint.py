import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Boolean, Enum, ForeignKey

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Complaint(Base):
    """6.7 申訴處理紀錄（表頭）：由電訪紀錄中之申訴類型延伸建立，
    啟動2工作天初步紀錄、10工作天結案之時限機制（6.6）。"""

    __tablename__ = "complaints"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    contact_record_id = Column(String, ForeignKey("contact_records.id"), nullable=False)

    summary = Column(String)
    received_date = Column(Date, nullable=False)  # 時限起算點
    responsible_supervisor_id = Column(String, ForeignKey("users.id"), nullable=False)

    family_reply_confirmed = Column(Boolean, default=False)  # 結案前須為True
    closed_date = Column(Date)
    closed_result = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ComplaintStageType(str, enum.Enum):
    initial = "初步紀錄"
    followup = "追蹤紀錄"
    closing = "結案紀錄"


class ComplaintProgressEntry(Base):
    """申訴處理進度紀錄，可多筆：初步/追蹤/結案紀錄可為同一筆或分屬多筆。"""

    __tablename__ = "complaint_progress_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    complaint_id = Column(String, ForeignKey("complaints.id"), nullable=False)

    stage_type = Column(Enum(ComplaintStageType), nullable=False)
    content = Column(String, nullable=False)
    entered_by = Column(String, ForeignKey("users.id"), nullable=False)
    entered_at = Column(DateTime, default=datetime.utcnow)
