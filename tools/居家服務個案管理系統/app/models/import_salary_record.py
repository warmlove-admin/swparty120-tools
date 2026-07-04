import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class ImportSalaryRecord(Base):
    __tablename__ = "import_salary_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    caregiver_name_raw = Column(String, nullable=False)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    case_name_raw = Column(String, nullable=False)
    service_date = Column(Date, nullable=False, index=True)
    service_address = Column(String)
    hourly_wage = Column(Integer)
    fill_minutes = Column(Integer, default=0)
    total_minutes = Column(Integer, default=0)
    visit_order = Column(Integer, default=0)
    transfer_minutes = Column(Float, default=0.0)
    weighted_total = Column(Float, default=0.0)

    weekday_0_8 = Column(Integer, default=0)
    weekday_9_10 = Column(Integer, default=0)
    weekday_11_12 = Column(Integer, default=0)
    national_holiday_0_8 = Column(Integer, default=0)
    national_holiday_9_10 = Column(Integer, default=0)
    national_holiday_11_12 = Column(Integer, default=0)
    regular_off_0_2 = Column(Integer, default=0)
    regular_off_3_8 = Column(Integer, default=0)
    regular_off_9_10 = Column(Integer, default=0)
    regular_off_11_12 = Column(Integer, default=0)
    rest_day_0_2 = Column(Integer, default=0)
    rest_day_3_8 = Column(Integer, default=0)
    rest_day_9_10 = Column(Integer, default=0)
    rest_day_11_12 = Column(Integer, default=0)

    source_filename = Column(String)
    import_batch_id = Column(String, index=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    upload_user_id = Column(String, ForeignKey("users.id"))

    caregiver = relationship("User", foreign_keys=[caregiver_id])
    case = relationship("Case", foreign_keys=[case_id])
