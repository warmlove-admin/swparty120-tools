import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON

from app.database import Base
from app.models.assessment import RecordStatus  # 共用 草稿/待審/已核閱


def gen_uuid():
    return str(uuid.uuid4())


class RecordStatusLog(Base):
    """7.3/7.4 通用審核狀態流轉與版本快照紀錄。
    record_type+record_id 指向任何採用審核工作流的紀錄
    （assessment／contact_record／complaint_progress_entry...），
    避免每種紀錄各自重做一套審核機制。
    snapshot_content 僅於「主管退回」或「已核閱後申請修改」時填入，
    保留異動前完整內容供日後稽核（7.4）。"""

    __tablename__ = "record_status_logs"

    id = Column(String, primary_key=True, default=gen_uuid)

    record_type = Column(String, nullable=False)  # 如 "assessment", "contact_record"
    record_id = Column(String, nullable=False)

    from_status = Column(String)
    to_status = Column(String, nullable=False)

    changed_by = Column(String, ForeignKey("users.id"), nullable=False)
    change_note = Column(String)  # 主管退回原因等

    snapshot_content = Column(JSON)  # 異動前完整內容快照

    created_at = Column(DateTime, default=datetime.utcnow)
