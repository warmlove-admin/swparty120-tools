from __future__ import annotations

from enum import Enum
from typing import Iterable

from app.models.assessment import RecordStatus
from app.models.contact_record import ContactRecord, PhoneCallType
from app.models.record_status_log import RecordStatusLog
from app.models.user import User, UserRole


class DecisionLevel(str, Enum):
    manager = "主管決行"
    director = "主任決行"


ROLE_SEQUENCE = (UserRole.supervisor, UserRole.manager, UserRole.director)

CONTACT_DIRECTOR_TYPES = {
    PhoneCallType.caregiver_incident,
    PhoneCallType.caregiver_emergency,
    PhoneCallType.case_feedback,
    PhoneCallType.case_complaint,
}


def decision_level_for_record(record_type: str, record) -> DecisionLevel:
    if record_type == "contact_record" and isinstance(record, ContactRecord):
        if record.phone_call_type in CONTACT_DIRECTOR_TYPES:
            return DecisionLevel.director
    return DecisionLevel.manager


def final_reviewer_role(record_type: str, record) -> UserRole:
    level = decision_level_for_record(record_type, record)
    return UserRole.director if level == DecisionLevel.director else UserRole.manager


def _role_index(role: UserRole) -> int:
    return ROLE_SEQUENCE.index(role)


def _user_role_from_log(log: RecordStatusLog) -> UserRole | None:
    if log.from_status == RecordStatus.draft.value and log.to_status == RecordStatus.pending.value:
        return UserRole.supervisor
    user = log.changed_by_user
    return user.role if user and user.role in ROLE_SEQUENCE else None


def active_workflow_logs(logs: Iterable[RecordStatusLog]) -> list[RecordStatusLog]:
    """Return logs after the latest return-to-draft; those are the only active stamps."""
    ordered = sorted(logs, key=lambda log: log.created_at)
    latest_reset_index = -1
    for index, log in enumerate(ordered):
        if (
            log.to_status == RecordStatus.draft.value
            and log.from_status in {RecordStatus.pending.value, RecordStatus.approved.value}
        ):
            latest_reset_index = index
    return ordered[latest_reset_index + 1:]


def active_approval_logs(logs: Iterable[RecordStatusLog]) -> list[RecordStatusLog]:
    active = active_workflow_logs(logs)
    latest_by_role: dict[UserRole, RecordStatusLog] = {}
    for log in active:
        role = _user_role_from_log(log)
        if role and log.to_status in {RecordStatus.pending.value, RecordStatus.approved.value}:
            latest_by_role[role] = log
    return [latest_by_role[role] for role in ROLE_SEQUENCE if role in latest_by_role]


def role_has_active_approval(logs: Iterable[RecordStatusLog], role: UserRole) -> bool:
    return any(_user_role_from_log(log) == role for log in active_approval_logs(logs))


def next_reviewer_role(record_type: str, record, logs: Iterable[RecordStatusLog]) -> UserRole | None:
    final_role = final_reviewer_role(record_type, record)
    for role in ROLE_SEQUENCE[1 : _role_index(final_role) + 1]:
        if not role_has_active_approval(logs, role):
            return role
    return None


def approval_status_after_role(record_type: str, record, role: UserRole) -> RecordStatus:
    return RecordStatus.approved if role == final_reviewer_role(record_type, record) else RecordStatus.pending


def can_user_approve(record_type: str, record, logs: Iterable[RecordStatusLog], user: User) -> bool:
    return record.status == RecordStatus.pending and user.role == next_reviewer_role(record_type, record, logs)


def can_user_return(record_type: str, record, logs: Iterable[RecordStatusLog], user: User) -> bool:
    if record.status not in {RecordStatus.pending, RecordStatus.approved}:
        return False
    active_roles = [_user_role_from_log(log) for log in active_approval_logs(logs)]
    if user.role not in {UserRole.manager, UserRole.director}:
        return False
    if user.role == UserRole.manager:
        return UserRole.supervisor in active_roles or UserRole.manager in active_roles
    if user.role == UserRole.director:
        return bool(active_roles)
    return False


def workflow_status_label(record_type: str, record, logs: Iterable[RecordStatusLog]) -> str:
    next_role = next_reviewer_role(record_type, record, logs)
    if record.status == RecordStatus.approved:
        return "已決行"
    if record.status == RecordStatus.pending and next_role:
        return f"待{next_role.value}核章"
    return record.status.value
