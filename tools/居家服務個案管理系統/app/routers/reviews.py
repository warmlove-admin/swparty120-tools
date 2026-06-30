from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.assessment import Assessment, RecordStatus
from app.models.case import Case
from app.models.care_plan import CarePlan
from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.contact_record import ContactRecord
from app.models.goal import Goal, GoalProgressLog
from app.models.record_status_log import RecordStatusLog
from app.models.complaint_report import ComplaintReport, ComplaintReportStatus
from app.models.user import User, UserRole
from app.services.assessment_summary import build_assessment_summary
from app.services.complaint_workflow import can_user_approve_complaint_stage, can_user_return_complaint_stage
from app.services.contact_review import required_final_reviewer_label
from app.services.record_workflow import (
    approval_status_after_role,
    can_user_approve,
    can_user_return,
    final_reviewer_role,
    next_reviewer_role,
    workflow_status_label,
)
from app.services.signature_stamps import review_rows
from app.services.schedule_formalization import PENDING_FORMALIZATION, sync_case_external_schedule_status

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

REVIEW_ROLES = (UserRole.manager, UserRole.director)
WORKFLOW_ROLES = (UserRole.supervisor, UserRole.manager, UserRole.director)
DEFAULT_APPROVAL_NOTE = "審閱通過"


def _assessment_or_404(db: Session, case_id: str, assessment_id: str) -> Assessment:
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.case_id == case_id,
    ).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    return assessment


def _can_manage_assessment(user: User, assessment: Assessment) -> bool:
    return user.role in REVIEW_ROLES or assessment.assessor_id == user.id


def _can_review(user: User) -> bool:
    return user.role in REVIEW_ROLES


def _assessment_workflow_permissions(user: User, assessment: Assessment, latest_approval_log: RecordStatusLog | None) -> dict:
    workflow_logs = getattr(assessment, "_workflow_logs", [])
    next_reviewer = next_reviewer_role("assessment", assessment, workflow_logs)
    can_manage = _can_manage_assessment(user, assessment)
    can_review = _can_review(user)
    return {
        "can_submit": assessment.status == RecordStatus.draft and can_manage,
        "can_approve": can_review and can_user_approve("assessment", assessment, workflow_logs, user),
        "can_return_pending": can_review and can_user_return("assessment", assessment, workflow_logs, user),
        "can_request_edit_after_approval": can_review and can_user_return("assessment", assessment, workflow_logs, user),
        "can_edit_approval_note": bool(
            latest_approval_log
            and assessment.status == RecordStatus.approved
            and can_review
            and latest_approval_log.changed_by == user.id
        ),
        "next_reviewer_label": next_reviewer.value if next_reviewer else None,
        "next_action_label": "決行" if next_reviewer and next_reviewer == final_reviewer_role("assessment", assessment) else "核章",
        "workflow_status_label": workflow_status_label("assessment", assessment, workflow_logs),
        "role_note": {
            UserRole.supervisor: "居督可送審自己負責的草稿評估，已核閱後若需補正可退回草稿重修。",
            UserRole.manager: "主管可核閱或退回待審評估，也可處理定案後退回重修。",
            UserRole.director: "主任可核閱或退回待審評估，也可處理定案後退回重修。",
        }.get(user.role, "目前角色沒有評估簽核操作權限。"),
    }


def _latest_workflow_logs(db: Session, assessment_ids: list[str]) -> dict[str, RecordStatusLog]:
    if not assessment_ids:
        return {}
    logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment",
        RecordStatusLog.record_id.in_(assessment_ids),
    ).order_by(RecordStatusLog.created_at.desc()).all()
    latest_by_record = {}
    for log in logs:
        latest_by_record.setdefault(log.record_id, log)
    return latest_by_record


def _workflow_label(log: RecordStatusLog) -> str:
    if log.to_status == RecordStatus.pending.value:
        return "送審說明"
    if log.to_status == RecordStatus.draft.value:
        if log.from_status == RecordStatus.approved.value:
            return "定案後退回重修原因"
        return "審核退回補正原因"
    if log.to_status == RecordStatus.approved.value:
        return "核閱意見"
    return "流程說明"


def _workflow_action_label(log: RecordStatusLog) -> str:
    if log.from_status == RecordStatus.draft.value and log.to_status == RecordStatus.pending.value:
        return "居督送審／重送審"
    if log.from_status == RecordStatus.pending.value and log.to_status == RecordStatus.draft.value:
        return "審核退回補正"
    if log.from_status == RecordStatus.pending.value and log.to_status == RecordStatus.approved.value:
        return "主管／主任核閱"
    if log.from_status == RecordStatus.approved.value and log.to_status == RecordStatus.draft.value:
        return "定案後退回重修"
    return f"{log.from_status or '新建'} → {log.to_status}"


def _approval_note_or_default(note: str) -> str:
    return note.strip()


def _pending_schedule_formalization_count(db: Session, case_id: str) -> int:
    sync_case_external_schedule_status(db, case_id)
    return db.query(CaregiverServiceRecord).filter(
        CaregiverServiceRecord.case_id == case_id,
        CaregiverServiceRecord.formalization_status == PENDING_FORMALIZATION,
    ).count()


def _todo_context(db: Session, user: User) -> dict:
    pending_assessments = []
    returned_assessments = []
    contact_drafts = []
    contact_pending = []
    contact_followups = []
    complaint_todos = []
    if user.role in REVIEW_ROLES:
        pending_candidates = db.query(Assessment).filter(
            Assessment.status == RecordStatus.pending
        ).order_by(Assessment.updated_at.asc()).all()
        pending_assessments = []
        for assessment in pending_candidates:
            logs = db.query(RecordStatusLog).filter(
                RecordStatusLog.record_type == "assessment",
                RecordStatusLog.record_id == assessment.id,
            ).order_by(RecordStatusLog.created_at.asc()).all()
            if can_user_approve("assessment", assessment, logs, user):
                pending_assessments.append(assessment)
        contact_pending = [
            record for record in db.query(ContactRecord).join(Case).filter(
                ContactRecord.status == RecordStatus.pending
            ).order_by(ContactRecord.updated_at.asc()).all()
            if can_user_approve(
                "contact_record",
                record,
                db.query(RecordStatusLog).filter(
                    RecordStatusLog.record_type == "contact_record",
                    RecordStatusLog.record_id == record.id,
                ).order_by(RecordStatusLog.created_at.asc()).all(),
                user,
            )
        ]
    if user.role in WORKFLOW_ROLES:
        returned_query = db.query(Assessment).join(Case).filter(
            Assessment.status == RecordStatus.draft
        )
        if user.role == UserRole.supervisor:
            returned_query = returned_query.filter(
                or_(Assessment.assessor_id == user.id, Case.primary_supervisor_id == user.id)
            )
        returned_candidates = returned_query.order_by(Assessment.updated_at.desc()).all()
        latest_logs = _latest_workflow_logs(db, [assessment.id for assessment in returned_candidates])
        returned_assessments = [
            {"assessment": assessment, "return_log": latest_logs[assessment.id]}
            for assessment in returned_candidates
            if assessment.id in latest_logs
            and latest_logs[assessment.id].to_status == RecordStatus.draft.value
            and latest_logs[assessment.id].from_status in {RecordStatus.pending.value, RecordStatus.approved.value}
        ]
        contact_draft_query = db.query(ContactRecord).join(Case).filter(
            ContactRecord.status == RecordStatus.draft
        )
        if user.role == UserRole.supervisor:
            contact_draft_query = contact_draft_query.filter(Case.primary_supervisor_id == user.id)
        contact_drafts = contact_draft_query.order_by(ContactRecord.updated_at.desc()).all()
        contact_followup_query = db.query(ContactRecord).join(Case).filter(
            or_(
                ContactRecord.followup_required == True,  # noqa: E712
                ContactRecord.followup_required == 1,
                ContactRecord.followup_required == "1",
                ContactRecord.followup_required == "true",
                ContactRecord.followup_required == "True",
            ),
            ContactRecord.followup_completed_at.is_(None),
        )
        if user.role == UserRole.supervisor:
            contact_followup_query = contact_followup_query.filter(Case.primary_supervisor_id == user.id)
        contact_followups = contact_followup_query.order_by(ContactRecord.contact_date.desc(), ContactRecord.updated_at.desc()).all()
    if user.role in [UserRole.supervisor, UserRole.manager, UserRole.director]:
        complaint_query = db.query(ComplaintReport).filter(
            ComplaintReport.status.in_([
                ComplaintReportStatus.submitted,
                ComplaintReportStatus.in_review,
                ComplaintReportStatus.final_pending,
                ComplaintReportStatus.final_returned,
                ComplaintReportStatus.reply_pending,
                ComplaintReportStatus.reply_returned,
            ]),
        )
        if user.role == UserRole.supervisor:
            complaint_query = complaint_query.filter(
                (ComplaintReport.assigned_reviewer_id == user.id)
                | (ComplaintReport.responsible_user_id == user.id)
            )
        complaint_candidates = complaint_query.order_by(ComplaintReport.initial_record_due_date.asc(), ComplaintReport.submitted_at.asc()).all()
        complaint_todos = [
            report for report in complaint_candidates
            if (
                user.role == UserRole.manager
                or report.assigned_reviewer_id == user.id
                or report.responsible_user_id == user.id
                or report.submit_to_role == "主任"
                or any(
                    can_user_approve_complaint_stage(db, report, stage, user)
                    or can_user_return_complaint_stage(db, report, stage, user)
                    for stage in ("initial", "final", "reply")
                )
            )
        ]
    latest_pending_logs = _latest_workflow_logs(db, [assessment.id for assessment in pending_assessments])
    return {
        "pending_assessments": pending_assessments,
        "pending_logs": latest_pending_logs,
        "returned_assessments": returned_assessments,
        "contact_drafts": contact_drafts,
        "contact_pending": contact_pending,
        "contact_followups": contact_followups,
        "complaint_todos": complaint_todos,
        "required_final_reviewer_label": required_final_reviewer_label,
        "workflow_status_label": workflow_status_label,
        "next_reviewer_role": next_reviewer_role,
        "record_todo_count": len(pending_assessments) + len(returned_assessments) + len(contact_drafts) + len(contact_pending),
        "followup_todo_count": len(contact_followups) + len(complaint_todos),
    }


def _assessment_trends(db: Session, case_id: str) -> list[dict]:
    ordered_assessments = db.query(Assessment).filter(
        Assessment.case_id == case_id
    ).order_by(Assessment.assessment_date).all()
    trends = []
    previous = None
    for item in ordered_assessments:
        items_by_code = {assessment_item.item_code: assessment_item for assessment_item in item.items}
        burden_item = items_by_code.get("FAM_burden_level")
        env_risk_item = items_by_code.get("ENV_risk_level")
        trends.append({
            "assessment": item,
            "adl_change": item.adl_total_score - previous.adl_total_score if previous and item.adl_total_score is not None and previous.adl_total_score is not None else None,
            "iadl_change": item.iadl_total_score - previous.iadl_total_score if previous and item.iadl_total_score is not None and previous.iadl_total_score is not None else None,
            "outing_change": item.outing_frequency - previous.outing_frequency if previous and item.outing_frequency is not None and previous.outing_frequency is not None else None,
            "burden_level": burden_item.text_value if burden_item else None,
            "burden_note": burden_item.note if burden_item else None,
            "env_risk_level": env_risk_item.text_value if env_risk_item else None,
            "env_risk_note": env_risk_item.note if env_risk_item else None,
        })
        previous = item
    return trends


def _goal_plan_pairs(goals: list[Goal], plans: list[CarePlan]) -> list[dict]:
    pairs = []
    linked_plan_ids = set()
    for goal in goals:
        linked_plans = [plan for plan in plans if goal in plan.goals]
        linked_plan_ids.update(plan.id for plan in linked_plans)
        pairs.append({"goal": goal, "plans": linked_plans})
    unlinked_plans = [plan for plan in plans if plan.id not in linked_plan_ids]
    if unlinked_plans:
        pairs.append({"goal": None, "plans": unlinked_plans})
    return pairs


def _continuation_label(goal: Goal) -> str:
    source = goal
    visited = set()
    while source.predecessor_goal and source.id not in visited:
        visited.add(source.id)
        source = source.predecessor_goal
    if source.origin_assessment:
        return f"延續 {source.origin_assessment.assessment_date} {source.origin_assessment.assessment_type.value}目標"
    return "延續既有目標"


def _write_status_log(
    db: Session,
    assessment: Assessment,
    user: User,
    to_status: RecordStatus,
    change_note: str | None = None,
    snapshot_content: dict | None = None,
):
    db.add(RecordStatusLog(
        record_type="assessment",
        record_id=assessment.id,
        from_status=assessment.status.value,
        to_status=to_status.value,
        changed_by=user.id,
        change_note=change_note or None,
        snapshot_content=snapshot_content,
    ))
    assessment.status = to_status


@router.get("/todos", response_class=HTMLResponse)
def todo_center(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        request,
        "review_queue.html",
        {"user": user, **_todo_context(db, user)},
    )


@router.get("/reviews", response_class=HTMLResponse)
def review_queue(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return todo_center(request, db, user)


@router.get("/cases/{case_id}/assessments/{assessment_id}/review-preview", response_class=HTMLResponse)
def review_preview(
    case_id: str,
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WORKFLOW_ROLES)),
):
    assessment = _assessment_or_404(db, case_id, assessment_id)
    case = assessment.case
    previous_assessment = db.query(Assessment).filter(
        Assessment.case_id == case_id,
        Assessment.assessment_date < assessment.assessment_date,
    ).order_by(Assessment.assessment_date.desc()).first()
    goals = db.query(Goal).filter(
        Goal.case_id == case_id,
        Goal.origin_assessment_id == assessment.id,
    ).order_by(Goal.set_date.asc()).all()
    plans = db.query(CarePlan).filter(
        CarePlan.case_id == case_id,
        CarePlan.origin_assessment_id == assessment.id,
    ).all()
    goal_reviews = db.query(GoalProgressLog).filter(
        GoalProgressLog.assessment_id == assessment.id
    ).all()
    review_logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment",
        RecordStatusLog.record_id == assessment.id,
    ).order_by(RecordStatusLog.created_at.desc()).all()
    latest_approval_log = next((log for log in review_logs if log.to_status == RecordStatus.approved.value), None)
    assessment._workflow_logs = list(reversed(review_logs))
    workflow_permissions = _assessment_workflow_permissions(user, assessment, latest_approval_log)
    pending_schedule_formalization_count = _pending_schedule_formalization_count(db, case_id)
    workflow_logs = list(reversed(review_logs))
    override = next((item.note for item in assessment.items if item.item_code == "summary_override" and item.note), None)
    return templates.TemplateResponse(
        request,
        "review_preview.html",
        {
            "user": user,
            "case": case,
            "assessment": assessment,
            "previous_assessment": previous_assessment,
            "summary_text": override or build_assessment_summary(assessment),
            "goals": goals,
            "plans": plans,
            "goal_plan_pairs": _goal_plan_pairs(goals, plans),
            "continued_goal_ids": {goal.id for goal in goals if goal.predecessor_goal_id},
            "continued_goal_labels": {
                goal.id: _continuation_label(goal) for goal in goals if goal.predecessor_goal_id
            },
            "goal_reviews": goal_reviews,
            "review_logs": review_logs,
            "workflow_logs": workflow_logs,
            "active_review_rows": review_rows(workflow_logs, active_only=True),
            "workflow_label": _workflow_label,
            "workflow_action_label": _workflow_action_label,
            "latest_approval_log": latest_approval_log,
            "assessment_trends": _assessment_trends(db, case_id),
            "workflow_permissions": workflow_permissions,
            "pending_schedule_formalization_count": pending_schedule_formalization_count,
        },
    )


@router.post("/cases/{case_id}/assessments/{assessment_id}/submit-review")
def submit_assessment_for_review(
    case_id: str,
    assessment_id: str,
    revision_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WORKFLOW_ROLES)),
):
    assessment = _assessment_or_404(db, case_id, assessment_id)
    if not _can_manage_assessment(user, assessment):
        raise HTTPException(403, "沒有權限送審這筆評估")
    if assessment.status != RecordStatus.draft:
        raise HTTPException(400, "只有草稿評估可以送審")
    _write_status_log(db, assessment, user, RecordStatus.pending, revision_note.strip())
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}/assessments/{assessment_id}/review-preview", status_code=302)


@router.post("/cases/{case_id}/assessments/{assessment_id}/approve")
def approve_assessment(
    case_id: str,
    assessment_id: str,
    approval_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*REVIEW_ROLES)),
):
    assessment = _assessment_or_404(db, case_id, assessment_id)
    review_logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment",
        RecordStatusLog.record_id == assessment.id,
    ).order_by(RecordStatusLog.created_at.asc()).all()
    if assessment.status != RecordStatus.pending:
        raise HTTPException(400, "只有待審評估可以核閱")
    if not can_user_approve("assessment", assessment, review_logs, user):
        raise HTTPException(403, "此評估尚未輪到目前角色核章")
    pending_schedule_count = _pending_schedule_formalization_count(db, case_id)
    if pending_schedule_count:
        raise HTTPException(
            400,
            f"此個案仍有 {pending_schedule_count} 筆外部匯入班表待轉正式，請先至服務班表分頁完成更新後再核閱。",
        )
    next_status = approval_status_after_role("assessment", assessment, user.role)
    _write_status_log(db, assessment, user, next_status, _approval_note_or_default(approval_note))
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}/assessments/{assessment_id}/review-preview", status_code=302)


@router.post("/cases/{case_id}/assessments/{assessment_id}/return")
def return_assessment(
    case_id: str,
    assessment_id: str,
    return_reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*REVIEW_ROLES)),
):
    assessment = _assessment_or_404(db, case_id, assessment_id)
    review_logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment",
        RecordStatusLog.record_id == assessment.id,
    ).order_by(RecordStatusLog.created_at.asc()).all()
    reason = return_reason.strip()
    if not reason:
        raise HTTPException(400, "退回原因不可空白")
    if assessment.status not in {RecordStatus.pending, RecordStatus.approved}:
        raise HTTPException(400, "只有待審或已決行評估可以取消核章／退回")
    if not can_user_return("assessment", assessment, review_logs, user):
        raise HTTPException(403, "此評估不屬於目前角色取消核章／退回")
    _write_status_log(db, assessment, user, RecordStatus.draft, reason)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}/assessments/{assessment_id}/review-preview", status_code=302)


@router.post("/cases/{case_id}/assessments/{assessment_id}/request-edit")
def request_assessment_edit(
    case_id: str,
    assessment_id: str,
    edit_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WORKFLOW_ROLES)),
):
    assessment = _assessment_or_404(db, case_id, assessment_id)
    review_logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment",
        RecordStatusLog.record_id == assessment.id,
    ).order_by(RecordStatusLog.created_at.asc()).all()
    if not can_user_return("assessment", assessment, review_logs, user):
        raise HTTPException(403, "沒有權限取消核章這筆評估")
    if assessment.status not in {RecordStatus.pending, RecordStatus.approved}:
        raise HTTPException(400, "只有待審或已決行評估可以取消核章／退回")
    _write_status_log(db, assessment, user, RecordStatus.draft, edit_note.strip())
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}/assessments/{assessment_id}/review-preview", status_code=302)


@router.post("/cases/{case_id}/assessments/{assessment_id}/approval-note")
def update_approval_note(
    case_id: str,
    assessment_id: str,
    approval_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*REVIEW_ROLES)),
):
    assessment = _assessment_or_404(db, case_id, assessment_id)
    if assessment.status != RecordStatus.approved:
        raise HTTPException(400, "只有已核閱評估可以修正核閱意見")
    approval_log = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment",
        RecordStatusLog.record_id == assessment.id,
        RecordStatusLog.to_status == RecordStatus.approved.value,
        RecordStatusLog.changed_by == user.id,
    ).order_by(RecordStatusLog.created_at.desc()).first()
    if not approval_log:
        raise HTTPException(403, "只能修正自己的核閱意見")
    approval_log.change_note = _approval_note_or_default(approval_note)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}/assessments/{assessment_id}/review-preview", status_code=302)
