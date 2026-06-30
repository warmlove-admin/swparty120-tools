import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Enum, ForeignKey

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class LineMessageSource(str, enum.Enum):
    webhook = "Webhook"
    pasted = "人工貼上"


class LineMessage(Base):
    """LINE 原始訊息留存。

    Webhook 收到的群組訊息、或測試階段人工貼上的對話，都先存在這裡；
    後續分析服務再依日期與群組/個案彙整成電訪紀錄。
    """

    __tablename__ = "line_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=True)
    line_group_id = Column(String, ForeignKey("line_groups.id"), nullable=True)

    line_platform_group_id = Column(String)
    line_user_id = Column(String)
    sender_name = Column(String)
    message_type = Column(String, nullable=False, default="text")
    message_text = Column(String)
    event_timestamp = Column(DateTime, nullable=False)
    source = Column(Enum(LineMessageSource), nullable=False, default=LineMessageSource.webhook)

    created_at = Column(DateTime, default=datetime.utcnow)
