from __future__ import annotations

from app.models.contact_record import ContactRecord, PhoneCallType
from app.models.user import UserRole
from app.services.record_workflow import final_reviewer_role


MANAGER_REVIEW_TYPES = {
    PhoneCallType.case_service_change,
    PhoneCallType.case_service_quantity_change,
    PhoneCallType.case_care,
    PhoneCallType.organization_matching,
    PhoneCallType.organization_case_discussion,
}

DIRECTOR_REVIEW_TYPES = {
    PhoneCallType.caregiver_incident,
    PhoneCallType.caregiver_emergency,
    PhoneCallType.case_feedback,
    PhoneCallType.case_complaint,
}


def required_final_reviewer_role(record: ContactRecord) -> UserRole | None:
    return final_reviewer_role("contact_record", record)


def required_final_reviewer_label(record: ContactRecord) -> str:
    role = required_final_reviewer_role(record)
    return role.value if role else "主責居督定案"


def can_final_approve_contact(record: ContactRecord, role: UserRole) -> bool:
    required_role = required_final_reviewer_role(record)
    return bool(required_role and role == required_role)
