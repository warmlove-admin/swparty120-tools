import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Date, Enum, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class ComplaintReporterType(str, enum.Enum):
    employee_self = "員工本人申訴"
    service_recipient = "代服務對象申訴"


class ComplaintReportKind(str, enum.Enum):
    general = "一般申訴"
    sexual_harassment = "性騷擾申訴"
    sexual_assault = "性侵害申訴"


class ComplaintReportStatus(str, enum.Enum):
    submitted = "已送出"
    in_review = "處理中"
    final_pending = "處理結果待核"
    final_returned = "處理結果退回"
    reply_pending = "回覆待核"
    reply_returned = "回覆退回"
    replied = "已回覆"
    returned = "退回修正"
    closed = "已結案"


class EmployeeComplaintCategory(str, enum.Enum):
    management = "管理溝通"
    schedule = "班表／派班"
    payroll = "薪資／獎金"
    workplace = "職場互動"
    safety = "工作安全"
    other = "其他"


class RecipientComplaintCategory(str, enum.Enum):
    service_attitude = "服務態度"
    service_quality = "服務品質"
    schedule_change = "班表異動"
    care_communication = "照顧溝通"
    fee_or_item = "費用／服務項目"
    other = "其他"


class ComplaintReport(Base):
    __tablename__ = "complaint_reports"

    id = Column(String, primary_key=True, default=gen_uuid)
    report_kind = Column(Enum(ComplaintReportKind), nullable=False, default=ComplaintReportKind.general)
    reporter_type = Column(Enum(ComplaintReporterType), nullable=False)
    status = Column(Enum(ComplaintReportStatus), nullable=False, default=ComplaintReportStatus.submitted)

    submitted_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    received_date = Column(Date, nullable=False)
    initial_record_due_date = Column(Date, nullable=False)
    final_result_due_date = Column(Date, nullable=False)
    initial_record_content = Column(String)
    initial_record_submitted_at = Column(DateTime)
    initial_record_submitted_by = Column(String, ForeignKey("users.id"))
    initial_record_approved_at = Column(DateTime)
    initial_record_approved_by = Column(String, ForeignKey("users.id"))
    initial_record_returned_at = Column(DateTime)
    initial_record_return_note = Column(String)

    submit_to_role = Column(String)
    assigned_reviewer_id = Column(String, ForeignKey("users.id"))
    responsible_user_id = Column(String, ForeignKey("users.id"))

    case_id = Column(String, ForeignKey("cases.id"))
    complainant_name = Column(String)
    complainant_relation = Column(String)
    complainant_phone = Column(String)

    employee_category = Column(Enum(EmployeeComplaintCategory))
    recipient_category = Column(Enum(RecipientComplaintCategory))
    subject = Column(String, nullable=False)
    content = Column(String, nullable=False)
    expected_resolution = Column(String)
    incident_date = Column(Date)
    incident_location = Column(String)
    accused_name = Column(String)
    accused_relationship = Column(String)
    witness_info = Column(String)
    requested_support = Column(String)
    handling_note = Column(String)
    final_result_content = Column(String)
    final_result_submitted_at = Column(DateTime)
    final_result_submitted_by = Column(String, ForeignKey("users.id"))
    final_result_approved_at = Column(DateTime)
    final_result_approved_by = Column(String, ForeignKey("users.id"))
    final_result_returned_at = Column(DateTime)
    final_result_return_note = Column(String)
    reply_content = Column(String)
    reply_submitted_at = Column(DateTime)
    reply_submitted_by = Column(String, ForeignKey("users.id"))
    reply_approved_at = Column(DateTime)
    reply_approved_by = Column(String, ForeignKey("users.id"))
    reply_returned_at = Column(DateTime)
    reply_return_note = Column(String)
    reply_read_at = Column(DateTime)
    close_result = Column(String)
    closed_at = Column(DateTime)

    submitted_by = relationship("User", foreign_keys=[submitted_by_id])
    assigned_reviewer = relationship("User", foreign_keys=[assigned_reviewer_id])
    responsible_user = relationship("User", foreign_keys=[responsible_user_id])
    initial_record_author = relationship("User", foreign_keys=[initial_record_submitted_by])
    initial_record_approver = relationship("User", foreign_keys=[initial_record_approved_by])
    final_result_author = relationship("User", foreign_keys=[final_result_submitted_by])
    final_result_approver = relationship("User", foreign_keys=[final_result_approved_by])
    reply_author = relationship("User", foreign_keys=[reply_submitted_by])
    reply_approver = relationship("User", foreign_keys=[reply_approved_by])
    case = relationship("Case", foreign_keys=[case_id])
