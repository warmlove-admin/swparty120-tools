import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Date, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship as sa_relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class NhiDependent(Base):
    """健保眷屬加保資料。"""
    __tablename__ = "nhi_dependents"

    id = Column(String, primary_key=True, default=gen_uuid)
    employee_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    id_number = Column(String)
    dep_relationship = Column(String, nullable=False)  # 配偶/子女/父母/其他
    birth_date = Column(Date)
    enrollment_date = Column(Date, nullable=False)
    termination_date = Column(Date)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = sa_relationship("User", foreign_keys=[employee_id])

    RELATIONSHIP_LABELS = {
        "配偶": "配偶",
        "子女": "子女",
        "父母": "父母",
        "其他": "其他",
    }

    @property
    def relationship_label(self):
        return self.RELATIONSHIP_LABELS.get(self.dep_relationship, self.dep_relationship)
