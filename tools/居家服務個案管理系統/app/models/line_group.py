import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Enum, ForeignKey

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class LineGroupType(str, enum.Enum):
    case_group = "個案群組"        # @
    caregiver_group = "居服員群組"  # !
    org_group = "單位群組"          # *


class LineGroup(Base):
    """6.2 LINE群組命名規則對照表，供Webhook收集訊息時判別個案/居服員。"""

    __tablename__ = "line_groups"

    id = Column(String, primary_key=True, default=gen_uuid)
    line_group_id = Column(String, unique=True, nullable=False)  # LINE平台真實群組ID
    group_name = Column(String, nullable=False)
    group_type = Column(Enum(LineGroupType), nullable=False)

    case_id = Column(String, ForeignKey("cases.id"), nullable=True)
    caregiver_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
