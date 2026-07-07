import uuid
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Time
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class CaregiverServiceRecord(Base):
    """Imported actual/roster service visit used to link cases and caregivers."""

    __tablename__ = "caregiver_service_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    service_date = Column(Date, nullable=False, index=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    minutes = Column(Integer, nullable=False)
    case_name_raw = Column(String, nullable=False)
    caregiver_name_raw = Column(String, nullable=False)
    service_codes = Column(String)
    formalization_status = Column(String, nullable=False, default="external_import")
    funding_source = Column(String, nullable=False, default="補助")  # 補助 / 自費
    source_file = Column(String)
    note = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("Case", backref="caregiver_service_records")
    caregiver = relationship("User", foreign_keys=[caregiver_id])
