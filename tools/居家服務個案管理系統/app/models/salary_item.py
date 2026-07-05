import uuid
from sqlalchemy import Column, String, Integer, Boolean
from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class SalaryItem(Base):
    __tablename__ = "salary_items"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False, unique=True)
    category = Column(String, nullable=False)  # "earnings" or "deductions"
    frequency = Column(String, default="monthly")  # monthly, semi_annual, annual, irregular
    display_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
