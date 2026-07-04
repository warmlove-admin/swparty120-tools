import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Enum, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class CaseStatus(str, enum.Enum):
    active = "服務中"
    paused = "暫停中"
    closed = "已結案"


class PauseReasonType(str, enum.Enum):
    hospitalization = "住院／就醫"
    short_term_institution = "短期入住機構（如護理之家、安養機構）"
    abroad = "個案出國／返鄉"
    family_care = "家屬自行照顧一段時間"
    financial = "經濟因素暫停部分服務"
    unwilling = "個案／家屬無意願使用服務"
    foreign_caregiver = "自聘外籍看護照顧"
    no_staff = "單位無人力"
    service_mismatch = "服務不符合要求"
    other = "其他"


class CloseReasonType(str, enum.Enum):
    death = "個案死亡"
    long_term_institution = "入住機構（長期）"
    service_out_of_scope_refused = "服務超出規範，單位拒絕服務"
    switched_provider = "個案指定其他服務單位"
    voluntary_termination = "個案／家屬主動終止，不再使用服務"
    welfare_status_changed = "福利資格變更（不再符合長照給付資格）"
    moved_out_of_area = "遷出服務範圍（搬家）"
    lost_contact = "失聯／聯絡不上"
    other = "其他"


def gen_uuid():
    return str(uuid.uuid4())


class Case(Base):
    __tablename__ = "cases"

    id = Column(String, primary_key=True, default=gen_uuid)

    # 5.2 機構自訂欄位
    org_case_no = Column(String, unique=True, nullable=False)

    # 5.1 衛福部匯入欄位
    name = Column(String, nullable=False)
    # 不設unique：個案結案後若重新開案，會建立新的一筆case（同一身分證字號可有多筆紀錄）
    id_number = Column(String, nullable=False)
    birth_date = Column(Date)
    phone = Column(String)
    gender = Column(String)
    household_address = Column(String)
    residence_address = Column(String)
    ltc_welfare_status = Column(String)
    disability_category = Column(String)
    cms_level = Column(String)
    living_status = Column(String)
    residence_district = Column(String)
    a_unit_name = Column(String)
    case_manager_name = Column(String)
    case_manager_contact = Column(String)
    last_cms_assessment_date = Column(Date)

    # 5.2 機構自訂欄位（服務代碼改至4.4照顧計畫之服務安排設定，可多選B/G/SC）
    primary_supervisor_id = Column(String, ForeignKey("users.id"))
    open_date = Column(Date)
    line_group_id = Column(String)
    photo_path = Column(String)

    # 洗腎接送設定
    is_dialysis = Column(String, default="N")  # "Y" / "N"
    dialysis_hospital_address = Column(String)
    dialysis_direction = Column(String)  # "送" / "接" / "送+接"

    # 個案狀態流動：服務中 / 暫停中 / 已結案
    status = Column(Enum(CaseStatus), nullable=False, default=CaseStatus.active)

    pause_date = Column(Date)
    pause_reason_type = Column(Enum(PauseReasonType))
    pause_reason_note = Column(String)
    resume_date = Column(Date)

    close_date = Column(Date)
    close_reason_type = Column(Enum(CloseReasonType))
    close_reason_note = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    primary_supervisor = relationship("User", foreign_keys=[primary_supervisor_id])
