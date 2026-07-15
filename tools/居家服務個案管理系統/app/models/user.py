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

    # 勞健保級距（從 Apollo 匯入或手動設定）
    insurance_labor_amount = Column(Integer, default=0)       # 勞保投保金額
    insurance_occupational_amount = Column(Integer, default=0) # 職災保險投保金額
    insurance_labor_pension_amount = Column(Integer, default=0) # 勞退投保金額
    labor_pension_employer_rate = Column(Integer, default=6)    # 勞退雇主提繳率 %
    labor_pension_personal_rate = Column(Integer, default=0)    # 勞退個人提繳率 %
    insurance_health_amount = Column(Integer, default=0)       # 健保投保金額
    health_dependents = Column(Integer, default=0)             # 健保眷屬加保人數
    has_exemption = Column(Boolean, default=False)             # 減免身分
    subsidy_rate = Column(Integer, default=0)                  # 補助費率 %
    insurance_note = Column(String)                            # 保險備註
    insurance_effective_year = Column(Integer, default=0)      # 生效年
    insurance_effective_month = Column(Integer, default=0)     # 生效月

    created_at = Column(DateTime, default=datetime.utcnow)

    supervisor = relationship("User", remote_side=[id], foreign_keys=[supervisor_id])

    @property
    def is_part_time(self) -> bool:
        if not self.work_weekdays:
            return False
        return sorted(self.work_weekdays) == [5, 6]

    def get_change_value(self, db, field_name: str, as_of=None) -> int | None:
        """從 employee_changes 查某欄位在 as_of 日期的最新值。"""
        from app.models.employee_change import EmployeeChange
        from sqlalchemy import desc
        q = (db.query(EmployeeChange)
             .filter(EmployeeChange.employee_id == self.id,
                     EmployeeChange.field_name == field_name))
        if as_of:
            q = q.filter(EmployeeChange.effective_date <= as_of)
        change = q.order_by(desc(EmployeeChange.effective_date)).first()
        return change.new_value if change else None
