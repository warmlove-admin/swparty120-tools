import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class AaCodeRecord(Base):
    __tablename__ = "aa_code_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    aa_code = Column(String, nullable=False, index=True)
    service_date = Column(Date, nullable=False)
    unit_price = Column(Integer, default=0)
    caregiver_share = Column(Integer, default=0)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    source_file = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)

    caregiver = relationship("User", foreign_keys=[caregiver_id])
    case = relationship("Case", foreign_keys=[case_id])


class AaImportRawRecord(Base):
    """AA碼原始匯入記錄：只存清冊原始資料（個案、碼別、日期、數量、金額），不含人員分配"""
    __tablename__ = "aa_import_raw_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    aa_code = Column(String, nullable=False, index=True)
    service_date = Column(Date, nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Integer, default=0)
    source_type = Column(String(20))
    source_file = Column(String)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("Case", foreign_keys=[case_id])


class Aa06CaseCondition(Base):
    __tablename__ = "aa06_case_conditions"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, unique=True, index=True)
    conditions = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", foreign_keys=[case_id])
