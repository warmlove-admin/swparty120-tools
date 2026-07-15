import uuid
from datetime import datetime, date

from sqlalchemy import Column, String, Integer, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class EmployeeChange(Base):
    """人事異動紀錄：保險級距、薪資、所得稅等變動統一追蹤。"""
    __tablename__ = "employee_changes"

    id = Column(String, primary_key=True, default=gen_uuid)
    employee_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    change_type = Column(String, nullable=False)       # insurance / salary / tax
    field_name = Column(String, nullable=False)         # insurance_labor_amount / hourly_wage / tax_dependents ...
    effective_date = Column(Date, nullable=False)       # 生效日
    old_value = Column(Integer, default=0)
    new_value = Column(Integer, default=0)
    source = Column(String, default="manual")           # apollo_import / manual / annual_adjustment
    created_by = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("User", foreign_keys=[employee_id])
    creator = relationship("User", foreign_keys=[created_by])

    # -- 欄位中文對照 --
    FIELD_LABELS = {
        "insurance_labor_amount": "勞保投保金額",
        "insurance_occupational_amount": "職災保險投保金額",
        "insurance_labor_pension_amount": "勞退投保金額",
        "labor_pension_employer_rate": "勞退雇主提繳率",
        "labor_pension_personal_rate": "勞退個人提繳率",
        "insurance_health_amount": "健保投保金額",
        "health_dependents": "健保眷屬人數",
        "has_exemption": "減免身分",
        "hourly_wage": "時薪",
        "tax_dependents": "所得稅扶養人數",
        "tax_rate": "所得稅稅率",
    }
    CHANGE_TYPE_LABELS = {
        "insurance": "勞健保",
        "salary": "薪資",
        "tax": "所得稅",
    }
    SOURCE_LABELS = {
        "apollo_import": "Apollo 匯入",
        "manual": "手動",
        "annual_adjustment": "年度調整",
    }

    @property
    def field_label(self):
        return self.FIELD_LABELS.get(self.field_name, self.field_name)

    @property
    def change_type_label(self):
        return self.CHANGE_TYPE_LABELS.get(self.change_type, self.change_type)

    @property
    def source_label(self):
        return self.SOURCE_LABELS.get(self.source, self.source)
