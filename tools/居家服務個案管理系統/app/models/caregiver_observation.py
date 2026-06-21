import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Boolean, Enum, ForeignKey

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class FunctionChangeTrend(str, enum.Enum):
    better = "變好"
    same = "持平"
    worse = "變差"
    unsure = "不確定"


class CaregiverObservation(Base):
    """3.9 居服員觀察項目：每位服務居服員每季可各自填寫一筆，
    供居督於定期評估前參考彙整（同個案可有多筆交叉觀察）。"""

    __tablename__ = "caregiver_observations"

    id = Column(String, primary_key=True, default=gen_uuid)
    assessment_id = Column(String, ForeignKey("assessments.id"), nullable=False)
    caregiver_user_id = Column(String, ForeignKey("users.id"), nullable=False)

    has_visitor_or_contact = Column(Boolean)        # 本週/本月是否有訪客或親友聯繫
    interaction_willingness = Column(String)         # 主動健談/需引導才回應/較少互動
    emotional_expression = Column(String)             # 看起來開心/普通/低落沉默
    behavior_observed = Column(Boolean)               # 是否觀察到遊走/抗拒照護等行為
    behavior_note = Column(String)

    function_change_trend = Column(Enum(FunctionChangeTrend))
    function_change_note = Column(String)

    filled_at = Column(DateTime, default=datetime.utcnow)
