import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class CaregiverTransfer(Base):
    __tablename__ = "caregiver_transfers"

    id = Column(String, primary_key=True, default=gen_uuid)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    service_date = Column(Date, nullable=False, index=True)
    from_visit_id = Column(String, ForeignKey("caregiver_service_records.id"), nullable=False)
    to_visit_id = Column(String, ForeignKey("caregiver_service_records.id"), nullable=False)
    from_case_name = Column(String, nullable=False)
    to_case_name = Column(String, nullable=False)
    from_address = Column(String)
    to_address = Column(String)
    transfer_km = Column(Float, default=0.0)
    transfer_minutes = Column(Float, default=0.0)
    status = Column(String, nullable=False, default="PENDING")
    error_message = Column(String)
    calculated_at = Column(DateTime)

    caregiver = relationship("User", foreign_keys=[caregiver_id])
    from_visit = relationship("CaregiverServiceRecord", foreign_keys=[from_visit_id])
    to_visit = relationship("CaregiverServiceRecord", foreign_keys=[to_visit_id])
