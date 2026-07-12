import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, JSON, String, Date, DateTime, Enum, Boolean, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    caregiver = "居服員"
    supervisor = "居督"
    manager = "主管"
    director = "主任"
    accountant = "會計"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)

    employee_no = Column(String)
    id_number = Column(String)
    gender = Column(String)
    birth_date = Column(Date)
    phone = Column(String)
    mobile = Column(String)
    email = Column(String)
    address = Column(String)
    job_title = Column(String)
    employment_status = Column(String)
    hire_date = Column(Date)
    termination_date = Column(Date)
    supervisor_id = Column(String, ForeignKey("users.id"))
    languages = Column(String)
    emergency_contact_name = Column(String)
    emergency_contact_relation = Column(String)
    emergency_contact_phone = Column(String)
    note = Column(String)

    regular_off_weekday = Column(Integer)
    rest_weekday = Column(Integer)
    hourly_wage = Column(Integer)
    work_weekdays = Column(JSON)
    force_overtime_weekend = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    supervisor = relationship("User", remote_side=[id], foreign_keys=[supervisor_id])

    @property
    def is_part_time(self) -> bool:
        if not self.work_weekdays:
            return False
        return sorted(self.work_weekdays) == [5, 6]
