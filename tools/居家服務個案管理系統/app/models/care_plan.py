import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Table
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


# 照顧計畫與照顧目標為多對多：同一實際服務（如每週3次協助沐浴）
# 可同時對應多個目標，避免在多個目標下各自複製一筆造成工時重複計算。
care_plan_goals = Table(
    "care_plan_goals",
    Base.metadata,
    Column("care_plan_id", String, ForeignKey("care_plans.id"), primary_key=True),
    Column("goal_id", String, ForeignKey("goals.id"), primary_key=True),
)


class CarePlan(Base):
    """4.4 照顧計畫之具體服務安排，歸屬個案（非單一目標），
    可關聯一個以上的照顧目標，作為未來排班模組資料來源。

    服務內容改為勾選長照給付碼別（BA/GA/SC），而非自由輸入文字，
    每個碼別有固定單位分鐘數，coded_services存放：
    [{"code": "BA01", "name": "基本身體清潔", "quantity": 3, "minutes_per_unit": 30, "total_minutes": 90}, ...]
    週期（如週一三五）與服務時間留給未來「建立服務班表」功能，
    本階段照顧計畫只回答「做哪些服務項目」這個臨床判斷，不涉及排班。
    """

    __tablename__ = "care_plans"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)

    coded_services = Column(JSON, nullable=False)  # 碼別清單，見上方說明
    assigned_caregiver_id = Column(String, ForeignKey("users.id"))
    note = Column(String)  # 補充說明（選填）

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", backref="care_plans")
    goals = relationship("Goal", secondary=care_plan_goals, backref="care_plans")
    assigned_caregiver = relationship("User", foreign_keys=[assigned_caregiver_id])
