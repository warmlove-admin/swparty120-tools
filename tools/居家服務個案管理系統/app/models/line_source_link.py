import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Enum, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class LineSourceKind(str, enum.Enum):
    user = "一對一"
    group = "群組"
    room = "多人聊天室"


class LineSourceCategory(str, enum.Enum):
    case_family = "個案／家屬"
    caregiver = "居服員"
    organization = "A單位"
    unknown = "未分類"


class LineSourceLink(Base):
    """LINE 來源與個案的對應。

    同一個案可有多個來源：個案本人、家屬、服務群組、居服員群組等。
    """

    __tablename__ = "line_source_links"

    id = Column(String, primary_key=True, default=gen_uuid)
    source_kind = Column(Enum(LineSourceKind), nullable=False)
    source_id = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    category = Column(Enum(LineSourceCategory), nullable=False, default=LineSourceCategory.unknown)
    relation_label = Column(String)

    case_id = Column(String, ForeignKey("cases.id"), nullable=True)
    caregiver_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", foreign_keys=[case_id])
    caregiver = relationship("User", foreign_keys=[caregiver_user_id])
