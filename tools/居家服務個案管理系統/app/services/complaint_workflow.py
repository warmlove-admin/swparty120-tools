from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.complaint_report import ComplaintReport
from app.models.record_status_log import RecordStatusLog
from app.models.user import User, UserRole
from app.services.signature_stamps import stamp_for_user


COMPLAINT_REVIEW_ROLES = (UserRole.manager, UserRole.director)

STAGE_META = {
    "initial": {
        "submitted_at": "initial_record_submitted_at",
        "submitted_by": "initial_record_submitted_by",
        "approved_at": "initial_record_approved_at",
        "submit_label": "初次處理紀錄已送出",
        "return_label": "初次處理紀錄退件",
        "approval_labels": {
            UserRole.manager: "第一次處理紀錄主管核章",
            UserRole.director: "第一次處理紀錄主任決行",
        },
    },
    "final": {
        "submitted_at": "final_result_submitted_at",
        "submitted_by": "final_result_submitted_by",
        "approved_at": "final_result_approved_at",
        "submit_label": "最終處理結果送出陳核",
        "return_label": "最終處理結果退回",
        "approval_labels": {
            UserRole.manager: "處理結果主管核章",
            UserRole.director: "處理結果主任決行",
        },
    },
    "reply": {
        "submitted_at": "reply_submitted_at",
        "submitted_by": "reply_submitted_by",
        "approved_at": "reply_approved_at",
        "submit_label": "回覆紀錄送出陳核",
        "return_label": "回覆紀錄退回",
        "approval_labels": {
            UserRole.manager: "回覆紀錄主管核章",
            UserRole.director: "回覆紀錄主任決行",
        },
    },
}


def _stage_meta(stage: str) -> dict:
    if stage not in STAGE_META:
        raise ValueError(f"Unknown complaint stage: {stage}")
    return STAGE_META[stage]


def complaint_stage_author(db: Session, report: ComplaintReport, stage: str) -> User | None:
    author_id = getattr(report, _stage_meta(stage)["submitted_by"])
    if not author_id:
        return None
    return db.query(User).filter(User.id == author_id, User.is_active.is_(True)).first()


def required_roles_for_stage(db: Session, report: ComplaintReport, stage: str) -> list[UserRole]:
    author = complaint_stage_author(db, report, stage)
    if not author:
        return []
    if author.role == UserRole.manager:
        return [UserRole.director]
    if author.role == UserRole.director:
        return [UserRole.director]
    return [UserRole.manager, UserRole.director]


def complaint_stage_logs(db: Session, report: ComplaintReport, stage: str) -> list[RecordStatusLog]:
    meta = _stage_meta(stage)
    logs = (
        db.query(RecordStatusLog)
        .filter(RecordStatusLog.record_type == "complaint_report", RecordStatusLog.record_id == report.id)
        .order_by(RecordStatusLog.created_at.asc())
        .all()
    )
    latest_submit_index = -1
    for index, log in enumerate(logs):
        if log.to_status == meta["submit_label"]:
            latest_submit_index = index
    if latest_submit_index < 0:
        return []
    return logs[latest_submit_index + 1 :]


def approved_roles_for_stage(db: Session, report: ComplaintReport, stage: str) -> list[UserRole]:
    labels = _stage_meta(stage)["approval_labels"]
    approved_roles = []
    for log in complaint_stage_logs(db, report, stage):
        for role, label in labels.items():
            if log.to_status == label and role not in approved_roles:
                approved_roles.append(role)
    return approved_roles


def next_complaint_reviewer_role(db: Session, report: ComplaintReport, stage: str) -> UserRole | None:
    meta = _stage_meta(stage)
    if not getattr(report, meta["submitted_at"]) or getattr(report, meta["approved_at"]):
        return None
    approved_roles = set(approved_roles_for_stage(db, report, stage))
    for role in required_roles_for_stage(db, report, stage):
        if role not in approved_roles:
            return role
    return None


def is_final_role_for_stage(db: Session, report: ComplaintReport, stage: str, role: UserRole) -> bool:
    required_roles = required_roles_for_stage(db, report, stage)
    return bool(required_roles) and role == required_roles[-1]


def can_user_approve_complaint_stage(db: Session, report: ComplaintReport, stage: str, user: User) -> bool:
    next_role = next_complaint_reviewer_role(db, report, stage)
    if user.role != next_role:
        return False
    author = complaint_stage_author(db, report, stage)
    if author and author.id == user.id and user.role != UserRole.director:
        return False
    return True


def can_user_return_complaint_stage(db: Session, report: ComplaintReport, stage: str, user: User) -> bool:
    if user.role not in COMPLAINT_REVIEW_ROLES:
        return False
    if not getattr(report, _stage_meta(stage)["submitted_at"]) or getattr(report, _stage_meta(stage)["approved_at"]):
        return False
    if user.role not in required_roles_for_stage(db, report, stage):
        return False
    author = complaint_stage_author(db, report, stage)
    return not (author and author.id == user.id and user.role != UserRole.director)


def complaint_stage_approval_label(stage: str, role: UserRole) -> str:
    return _stage_meta(stage)["approval_labels"][role]


def complaint_stage_next_label(db: Session, report: ComplaintReport, stage: str) -> str | None:
    role = next_complaint_reviewer_role(db, report, stage)
    if not role:
        return None
    return "主任決行" if role == UserRole.director else "主管核章"


def complaint_stage_signature_rows(db: Session, report: ComplaintReport, stage: str) -> list[dict]:
    meta = _stage_meta(stage)
    author = complaint_stage_author(db, report, stage)
    author_time = getattr(report, meta["submitted_at"])
    columns = {
        "author": {
            "label": "承辦",
            "name": author.display_name if author else "-",
            "role": author.role.value if author and author.role else "-",
            "note": "-",
            "time": author_time,
            "stamp": stamp_for_user(author),
        },
        "manager": {
            "label": "主管",
            "name": "-",
            "role": "主管",
            "note": "-",
            "time": None,
            "stamp": None,
        },
        "director": {
            "label": "主任",
            "name": "-",
            "role": "主任",
            "note": "-",
            "time": None,
            "stamp": None,
        },
    }
    labels = meta["approval_labels"]
    for log in complaint_stage_logs(db, report, stage):
        user = log.changed_by_user
        if log.to_status == labels.get(UserRole.manager):
            key = "manager"
        elif log.to_status == labels.get(UserRole.director):
            key = "director"
        else:
            continue
        columns[key].update({
            "name": user.display_name if user else "-",
            "role": user.role.value if user and user.role else columns[key]["role"],
            "note": log.change_note or "-",
            "time": log.created_at,
            "stamp": stamp_for_user(user),
        })
    return [columns["author"], columns["manager"], columns["director"]]
