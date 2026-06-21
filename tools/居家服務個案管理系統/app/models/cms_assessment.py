import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, ForeignKey, JSON

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class CmsAssessment(Base):
    """5.1 / 3.8 CMS評估（A-K大題）匯入資料：類型B背景參考，不重複評估。
    CMS項目眾多且衛福部格式可能調整，因此用raw_data保留完整匯入內容，
    並把第3.8章特殊照護面常用對照欄位獨立出來方便查詢。"""

    __tablename__ = "cms_assessments"

    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)

    assessment_date = Column(Date, nullable=False)
    cms_level = Column(String)

    # 3.8 特殊照護面對照欄位
    d1_short_term_memory = Column(String)
    d1_sleep_status = Column(String)
    g1_pain = Column(String)
    g2_skin_wound = Column(String)
    g4a_weight_change = Column(String)
    g4c_food_intake_change = Column(String)
    g6_swallowing = Column(String)
    k5_caregiver_health = Column(String)

    raw_data = Column(JSON)  # 完整A-K大題匯入原始資料

    imported_at = Column(DateTime, default=datetime.utcnow)
