import uuid
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, JSON, String, Time
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class ServiceSchedule(Base):
    """A weekly recurring service arrangement derived from a care plan item."""

    __tablename__ = "service_schedules"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    care_plan_id = Column(String, ForeignKey("care_plans.id"), nullable=False)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    service_code = Column(String, nullable=False)
    service_name = Column(String, nullable=False)
    minutes = Column(Integer, nullable=False)
    weekdays = Column(JSON, nullable=False)  # Monday=0 through Sunday=6
    start_time = Column(Time, nullable=False)
    effective_from = Column(Date, nullable=False, default=date.today)
    effective_until = Column(Date)
    funding_source = Column(String, nullable=False, default="補助")  # 補助 / 自費
    note = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", backref="service_schedules")
    care_plan = relationship("CarePlan", backref="service_schedules")
    caregiver = relationship("User", foreign_keys=[caregiver_id])
