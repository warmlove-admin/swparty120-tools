import uuid
from datetime import date, datetime

from sqlalchemy import Column, String, Date, DateTime, ForeignKey, Integer

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class LineDailyAnalysis(Base):
    """每日 LINE 群組分析批次紀錄。

    用 case_id + analysis_date + line_platform_group_id 防止排程重跑時重複產生電訪紀錄。
    """

    __tablename__ = "line_daily_analyses"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    line_platform_group_id = Column(String)
    analysis_date = Column(Date, nullable=False)

    message_count = Column(Integer, nullable=False, default=0)
    contact_record_id = Column(String, ForeignKey("contact_records.id"), nullable=True)
    complaint_id = Column(String, ForeignKey("complaints.id"), nullable=True)
    status = Column(String, nullable=False, default="已產生")
    note = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
