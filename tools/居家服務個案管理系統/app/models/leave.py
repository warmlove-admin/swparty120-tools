import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Boolean, Enum, ForeignKey, Integer, Text, Float
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class LeaveType(Base):
    __tablename__ = "leave_types"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    code = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class LeaveRequestStatus(str, enum.Enum):
    pending = "待審核"
    approved = "已核准"
    rejected = "已駁回"


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(String, primary_key=True, default=gen_uuid)
    caregiver_id = Column(String, ForeignKey("users.id"), nullable=False)
    leave_type_id = Column(String, ForeignKey("leave_types.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    days = Column(Float, nullable=False)
    reason = Column(Text)
    status = Column(Enum(LeaveRequestStatus), nullable=False, default=LeaveRequestStatus.pending)
    reviewed_by = Column(String, ForeignKey("users.id"))
    reviewed_at = Column(DateTime)
    rejection_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    caregiver = relationship("User", foreign_keys=[caregiver_id])
    leave_type = relationship("LeaveType")
    reviewer = relationship("User", foreign_keys=[reviewed_by])
