import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class MonthlySalary(Base):
    __tablename__ = "monthly_salaries"

    id = Column(String, primary_key=True, default=gen_uuid)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)

    weighted_minutes_no_transport = Column(Float, default=0.0)
    salary_no_transport = Column(Integer, default=0)
    weighted_minutes_with_transport = Column(Float, default=0.0)
    salary_with_transport = Column(Integer, default=0)
    transport_allowance = Column(Integer, default=0)

    total_transfer_km = Column(Float, default=0.0)
    total_transfer_minutes = Column(Float, default=0.0)
    total_service_minutes = Column(Integer, default=0)

    long_term_bonus = Column(Integer, default=0)
    aa_bonus = Column(Integer, default=0)
    travel_allowance = Column(Integer, default=0)

    calculated_at = Column(DateTime, default=datetime.utcnow)
    calculated_by = Column(String, ForeignKey("users.id"))

    caregiver = relationship("User", foreign_keys=[caregiver_id])
    calculator = relationship("User", foreign_keys=[calculated_by])

    @property
    def total_salary(self):
        return (self.salary_with_transport or 0) + (self.long_term_bonus or 0) + (self.aa_bonus or 0)
