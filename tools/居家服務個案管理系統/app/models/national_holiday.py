import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Integer

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class NationalHoliday(Base):
    __tablename__ = "national_holidays"

    id = Column(String, primary_key=True, default=gen_uuid)
    holiday_date = Column(Date, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    year = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
