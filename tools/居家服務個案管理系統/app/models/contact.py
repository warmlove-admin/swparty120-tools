import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Boolean, Enum, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class ContactRole(str, enum.Enum):
    primary_contact = "主要聯絡人"
    primary_caregiver = "主要照顧者"
    secondary_caregiver = "次要照顧者"
    agent = "代理人"


class Contact(Base):
    """5.1 聯絡人／照顧者資料：一個個案可有多筆，依角色分類"""

    __tablename__ = "contacts"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)

    contact_role = Column(Enum(ContactRole), nullable=False)
    name = Column(String, nullable=False)
    id_number = Column(String)
    birth_date = Column(Date)
    relation = Column(String)
    phone = Column(String)
    gender = Column(String)
    is_cohabiting = Column(Boolean)
    note = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", backref="contacts")


class EmergencyContact(Base):
    """5.3 緊急聯絡人：可從contacts帶入，亦可自行新增，一個個案可有多筆"""

    __tablename__ = "emergency_contacts"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)

    # 若是從contacts帶入，記錄來源；自行新增則為null
    source_contact_id = Column(String, ForeignKey("contacts.id"), nullable=True)

    name = Column(String, nullable=False)
    phone = Column(String)
    relation = Column(String)
    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("Case", backref="emergency_contacts")
