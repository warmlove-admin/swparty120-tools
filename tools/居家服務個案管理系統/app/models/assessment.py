import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Date, DateTime, Integer, Float, Enum, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class AssessmentType(str, enum.Enum):
    intake = "開案評估"
    periodic = "定期評估"


class RecordStatus(str, enum.Enum):
    draft = "草稿"
    pending = "待審"
    approved = "已核閱"


class Assessment(Base):
    """第三章 類型A 機構自評（開案＋每季複評），表頭。
    各細項分數另存於 assessment_items，避免欄位數爆炸且方便未來增減評估項目。"""

    __tablename__ = "assessments"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)

    assessment_type = Column(Enum(AssessmentType), nullable=False)
    assessment_date = Column(Date, nullable=False)
    assessor_id = Column(String, ForeignKey("users.id"))

    adl_total_score = Column(Integer)   # 0-100
    iadl_total_score = Column(Integer)  # 0-100

    # 3.1 IADL外出頻率（次/週），開案存基準值，定期評估存當季值
    outing_frequency = Column(Float)

    status = Column(Enum(RecordStatus), nullable=False, default=RecordStatus.draft)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("Case", backref="assessments")
    items = relationship("AssessmentItem", backref="assessment", cascade="all, delete-orphan")


class DataSource(str, enum.Enum):
    institution_self = "居督評估"
    caregiver_observed = "居服員觀察"
    supervisor_confirmed = "居督確認／修正"


class AssessmentItem(Base):
    """評估細項（ADL/IADL/認知心理/家庭照顧者/居家環境/經濟資源/文化語言等）。
    用item_code識別項目，分數/量表結果存score_value(數字)或note(文字)，
    彈性設計以容納不同量表方向（數字分數、是否、等級文字）。"""

    __tablename__ = "assessment_items"

    id = Column(String, primary_key=True, default=gen_uuid)
    assessment_id = Column(String, ForeignKey("assessments.id"), nullable=False)

    domain = Column(String, nullable=False)     # 身體功能面/認知與心理面/...
    item_code = Column(String, nullable=False)  # 如 ADL_eating, COG_orientation
    score_value = Column(Float)                 # 數字分數（如ADL/IADL單項分數）
    text_value = Column(String)                 # 文字型量表結果（如：清醒/混亂）
    note = Column(String)                       # 開放文字備註
    data_source = Column(Enum(DataSource), default=DataSource.institution_self)

    created_at = Column(DateTime, default=datetime.utcnow)
