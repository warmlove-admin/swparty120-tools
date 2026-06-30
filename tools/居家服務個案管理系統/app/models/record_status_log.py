import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.assessment import RecordStatus  # 共用 草稿/待審/已核閱


def gen_uuid():
    return str(uuid.uuid4())


class RecordStatusLog(Base):
    """通用審核狀態流轉、意見與修正說明紀錄。
    record_type+record_id 指向任何採用審核工作流的紀錄
    （assessment／contact_record／complaint_progress_entry...），
    避免每種紀錄各自重做一套審核機制。
    以退回意見、居督修正說明與主管核閱意見作為可追溯依據。"""

    __tablename__ = "record_status_logs"

    id = Column(String, primary_key=True, default=gen_uuid)

    record_type = Column(String, nullable=False)  # 如 "assessment", "contact_record"
    record_id = Column(String, nullable=False)

    from_status = Column(String)
    to_status = Column(String, nullable=False)

    changed_by = Column(String, ForeignKey("users.id"), nullable=False)
    change_note = Column(String)  # 主管退回原因等

    snapshot_content = Column(JSON)  # 保留舊欄位相容性；目前審核以意見與修正歷程為準

    created_at = Column(DateTime, default=datetime.utcnow)

    changed_by_user = relationship("User", foreign_keys=[changed_by])
