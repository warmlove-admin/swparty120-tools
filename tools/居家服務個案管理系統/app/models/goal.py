import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Date, DateTime, Enum, ForeignKey, JSON,
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class GoalTemplate(Base):
    """4.2 目標庫題庫：依面向分類的目標模板，居督勾選後可編輯帶出文字。"""

    __tablename__ = "goal_templates"

    id = Column(String, primary_key=True, default=gen_uuid)
    domain = Column(String, nullable=False)
    template_name = Column(String, nullable=False)
    goal_description = Column(String, nullable=False)
    default_item_codes = Column(JSON)  # 預設關聯評估項目代碼清單


class GoalStatus(str, enum.Enum):
    in_progress = "進行中"
    achieved = "已達成"
    replaced = "已更換"


class Goal(Base):
    """4.1 照顧目標：聚焦單一評估面向，可關聯該面向內多個細項。"""

    __tablename__ = "goals"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    template_id = Column(String, ForeignKey("goal_templates.id"), nullable=True)

    domain = Column(String, nullable=False)
    description = Column(String, nullable=False)
    related_item_codes = Column(JSON)  # 關聯評估細項代碼清單

    set_date = Column(Date, nullable=False)
    status = Column(Enum(GoalStatus), nullable=False, default=GoalStatus.in_progress)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", backref="goals")
    template = relationship("GoalTemplate")


class AchievementLevel(str, enum.Enum):
    achieved = "達成"
    partially_achieved = "部分達成"
    not_achieved = "未達成"


class GoalDecision(str, enum.Enum):
    close = "結案"
    continue_existing = "繼續延用"
    replace = "更換為新目標"


class GoalProgressLog(Base):
    """4.3 每次定期評估時針對進行中目標所做的達成度判定紀錄。"""

    __tablename__ = "goal_progress_logs"

    id = Column(String, primary_key=True, default=gen_uuid)
    goal_id = Column(String, ForeignKey("goals.id"), nullable=False)
    assessment_id = Column(String, ForeignKey("assessments.id"), nullable=False)

    achievement_level = Column(Enum(AchievementLevel), nullable=False)
    decision = Column(Enum(GoalDecision), nullable=False)
    change_reason = Column(String)  # 更換或未達成持續時的必填原因
    system_reference_summary = Column(String)  # 系統整理之分數變化摘要，僅供參考

    judged_at = Column(DateTime, default=datetime.utcnow)
