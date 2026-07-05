import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class SalaryPayment(Base):
    __tablename__ = "salary_payments"

    id = Column(String, primary_key=True, default=gen_uuid)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    salary_item_id = Column(String, ForeignKey("salary_items.id"), nullable=False)
    amount = Column(Integer, default=0)
    notes = Column(String, default="")
    payment_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    caregiver = relationship("User", foreign_keys=[caregiver_id])
    salary_item = relationship("SalaryItem", foreign_keys=[salary_item_id])
