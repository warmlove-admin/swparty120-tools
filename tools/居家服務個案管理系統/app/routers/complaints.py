from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.case import Case, CaseStatus
from app.models.complaint_report import (
    ComplaintReport,
    ComplaintReportKind,
    ComplaintReporterType,
    ComplaintReportStatus,
    EmployeeComplaintCategory,
    RecipientComplaintCategory,
)
from app.models.record_status_log import RecordStatusLog
from app.models.user import User, UserRole
from app.services.complaint_workflow import (
    can_user_approve_complaint_stage,
    can_user_return_complaint_stage,
    complaint_stage_approval_label,
    complaint_stage_signature_rows,
    complaint_stage_next_label,
    is_final_role_for_stage,
)

router = APIRouter(prefix="/complaints")
templates = Jinja2Templates(directory="app/templates")


def _add_business_days(start_date: date, days: int) -> date:
    current = start_date
    remaining = days
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _add_months(start_date: date, months: int) -> date:
    month = start_date.month - 1 + months
    year = start_date.year + month // 12
    month = month % 12 + 1
    day = min(start_date.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _final_due_date(report_kind: ComplaintReportKind, received_date: date) -> date:
    if report_kind == ComplaintReportKind.general:
        return _add_business_days(received_date, 10)
    return _add_months(received_date, 2)


def _director(db: Session) -> User | None:
    return db.query(User).filter(User.role == UserRole.director, User.is_active.is_(True)).order_by(User.display_name).first()


def _case_primary_supervisor(db: Session, case_id: str | None) -> User | None:
    if not case_id:
        return None
    case = db.query(Case).filter(Case.id == case_id).first()
    if case and case.primary_supervisor_id:
        return db.query(User).filter(User.id == case.primary_supervisor_id, User.is_active.is_(True)).first()
    return None


def _can_handle_report(user: User, report: ComplaintReport) -> bool:
    if user.role in {UserRole.manager, UserRole.director}:
        return True
    return user.id in {report.assigned_reviewer_id, report.responsible_user_id}


def _can_view_report(user: User, report: ComplaintReport) -> bool:
    return user.id == report.submitted_by_id or _can_handle_report(user, report)


def _write_log(db: Session, report: ComplaintReport, user: User, to_status: str, note: str | None = None) -> None:
    db.add(RecordStatusLog(
        record_type="complaint_report",
        record_id=report.id,
        from_status=report.status.value if report.status else None,
        to_status=to_status,
        changed_by=user.id,
        change_note=note or None,
        created_at=datetime.utcnow(),
    ))


def _form_context(db: Session, user: User, error: str | None = None):
    return {
        "user": user,
        "error": error,
        "today": date.today(),
        "report_kinds": list(ComplaintReportKind),
        "reporter_types": list(ComplaintReporterType),
        "employee_categories": list(EmployeeComplaintCategory),
        "recipient_categories": list(RecipientComplaintCategory),
        "cases": (
            db.query(Case)
            .filter(Case.status != CaseStatus.closed)
            .order_by(Case.org_case_no.asc(), Case.name.asc())
            .all()
        ),
        "has_supervisor": bool(user.supervisor_id),
        "has_director": bool(_director(db)),
    }


@router.get("", response_class=HTMLResponse)
def complaint_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(ComplaintReport)
    if user.role == UserRole.caregiver:
        query = query.filter(
            ComplaintReport.submitted_by_id == user.id,
            ComplaintReport.reply_approved_at.isnot(None),
        )
    elif user.role == UserRole.supervisor:
        query = query.filter(
            (ComplaintReport.submitted_by_id == user.id)
            | (ComplaintReport.assigned_reviewer_id == user.id)
            | (ComplaintReport.responsible_user_id == user.id)
        )
    reports = query.order_by(ComplaintReport.submitted_at.desc()).limit(100).all()
    return templates.TemplateResponse(
        request,
        "complaint_reports_list.html",
        {"user": user, "reports": reports, "today": date.today(), "is_handler": user.role != UserRole.caregiver},
    )


@router.get("/new", response_class=HTMLResponse)
def complaint_new(
    request: Request,
    kind: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    context = _form_context(db, user)
    context["selected_kind"] = kind or ComplaintReportKind.general.value
    return templates.TemplateResponse(request, "complaint_report_form.html", context)


@router.post("/new")
def complaint_create(
    request: Request,
    report_kind: str = Form(...),
    reporter_type: str = Form(...),
    submit_to: str = Form("supervisor"),
    case_id: str = Form(""),
    complainant_name: str = Form(""),
    complainant_relation: str = Form(""),
    complainant_phone: str = Form(""),
    employee_category: str = Form(""),
    recipient_category: str = Form(""),
    subject: str = Form(...),
    content: str = Form(...),
    expected_resolution: str = Form(""),
    incident_date: str = Form(""),
    incident_location: str = Form(""),
    accused_name: str = Form(""),
    accused_relationship: str = Form(""),
    witness_info: str = Form(""),
    requested_support: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        parsed_report_kind = ComplaintReportKind(report_kind)
    except ValueError:
        raise HTTPException(400, "申訴表類型不正確")
    try:
        parsed_reporter_type = ComplaintReporterType(reporter_type)
    except ValueError:
        raise HTTPException(400, "申訴人類型不正確")

    subject = subject.strip()
    content = content.strip()
    if not subject or not content:
        return templates.TemplateResponse(
            request,
            "complaint_report_form.html",
            _form_context(db, user, "請填寫申訴主旨與申訴內容。"),
            status_code=400,
        )
    parsed_incident_date = date.fromisoformat(incident_date) if incident_date else None

    assigned_reviewer = None
    responsible_user = None
    parsed_employee_category = None
    parsed_recipient_category = None
    selected_case_id = None

    if parsed_reporter_type == ComplaintReporterType.employee_self:
        if employee_category:
            parsed_employee_category = EmployeeComplaintCategory(employee_category)
        if submit_to == "director":
            assigned_reviewer = _director(db)
        else:
            assigned_reviewer = db.query(User).filter(User.id == user.supervisor_id, User.is_active.is_(True)).first() if user.supervisor_id else None
        responsible_user = assigned_reviewer
    else:
        selected_case_id = case_id or None
        if not selected_case_id:
            return templates.TemplateResponse(
                request,
                "complaint_report_form.html",
                _form_context(db, user, "代服務對象申訴必須選擇個案。"),
                status_code=400,
            )
        if recipient_category:
            parsed_recipient_category = RecipientComplaintCategory(recipient_category)
        assigned_reviewer = _case_primary_supervisor(db, selected_case_id)
        responsible_user = assigned_reviewer

    today = date.today()
    final_due_date = _final_due_date(parsed_report_kind, today)
    report = ComplaintReport(
        report_kind=parsed_report_kind,
        reporter_type=parsed_reporter_type,
        status=ComplaintReportStatus.submitted,
        submitted_by_id=user.id,
        received_date=today,
        initial_record_due_date=_add_business_days(today, 2),
        final_result_due_date=final_due_date,
        submit_to_role="主任" if submit_to == "director" else "直屬主管",
        assigned_reviewer_id=assigned_reviewer.id if assigned_reviewer else None,
        responsible_user_id=responsible_user.id if responsible_user else None,
        case_id=selected_case_id,
        complainant_name=complainant_name.strip() or None,
        complainant_relation=complainant_relation.strip() or None,
        complainant_phone=complainant_phone.strip() or None,
        employee_category=parsed_employee_category,
        recipient_category=parsed_recipient_category,
        subject=subject,
        content=content,
        expected_resolution=expected_resolution.strip() or None,
        incident_date=parsed_incident_date,
        incident_location=incident_location.strip() or None,
        accused_name=accused_name.strip() or None,
        accused_relationship=accused_relationship.strip() or None,
        witness_info=witness_info.strip() or None,
        requested_support=requested_support.strip() or None,
    )
    db.add(report)
    db.commit()
    return RedirectResponse(url=f"/complaints?created={report.id}", status_code=302)


@router.get("/{report_id}", response_class=HTMLResponse)
def complaint_detail(
    report_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "找不到申訴單")
    if not _can_view_report(user, report):
        raise HTTPException(403, "權限不足")
    logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "complaint_report",
        RecordStatusLog.record_id == report.id,
    ).order_by(RecordStatusLog.created_at.asc()).all()
    if user.id == report.submitted_by_id and report.reply_approved_at and not report.reply_read_at:
        report.reply_read_at = datetime.utcnow()
        db.commit()
    return templates.TemplateResponse(
        request,
        "complaint_report_detail.html",
        {
            "user": user,
            "report": report,
            "logs": logs,
            "can_handle": _can_handle_report(user, report),
            "can_view_sensitive_content": _can_handle_report(user, report),
            "can_approve_initial": can_user_approve_complaint_stage(db, report, "initial", user),
            "can_return_initial": can_user_return_complaint_stage(db, report, "initial", user),
            "initial_next_label": complaint_stage_next_label(db, report, "initial"),
            "initial_signature_rows": complaint_stage_signature_rows(db, report, "initial"),
            "can_approve_final": can_user_approve_complaint_stage(db, report, "final", user),
            "can_return_final": can_user_return_complaint_stage(db, report, "final", user),
            "final_next_label": complaint_stage_next_label(db, report, "final"),
            "final_signature_rows": complaint_stage_signature_rows(db, report, "final"),
            "can_approve_reply": can_user_approve_complaint_stage(db, report, "reply", user),
            "can_return_reply": can_user_return_complaint_stage(db, report, "reply", user),
            "reply_next_label": complaint_stage_next_label(db, report, "reply"),
            "reply_signature_rows": complaint_stage_signature_rows(db, report, "reply"),
        },
    )


@router.post("/{report_id}/initial-record")
def complaint_initial_record(
    report_id: str,
    content: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if report.initial_record_submitted_at and not report.initial_record_approved_at and not report.initial_record_returned_at:
        raise HTTPException(400, "首次處理紀錄已送出待核，退件前不能重送")
    report.initial_record_content = content.strip()
    report.initial_record_submitted_at = datetime.utcnow()
    report.initial_record_submitted_by = user.id
    report.initial_record_approved_at = None
    report.initial_record_approved_by = None
    report.initial_record_returned_at = None
    report.initial_record_return_note = None
    report.status = ComplaintReportStatus.in_review
    _write_log(db, report, user, "初次處理紀錄已送出")
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/initial-record/approve")
def complaint_initial_record_approve(
    report_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if not can_user_approve_complaint_stage(db, report, "initial", user):
        raise HTTPException(403, "目前不是此第一次處理紀錄的核章角色")
    approval_label = complaint_stage_approval_label("initial", user.role)
    _write_log(db, report, user, approval_label, note.strip() or None)
    if not is_final_role_for_stage(db, report, "initial", user.role):
        db.commit()
        return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)
    report.initial_record_approved_at = datetime.utcnow()
    report.initial_record_approved_by = user.id
    report.status = ComplaintReportStatus.in_review
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/initial-record/return")
def complaint_initial_record_return(
    report_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if not can_user_return_complaint_stage(db, report, "initial", user):
        raise HTTPException(403, "目前不是此第一次處理紀錄的退件角色")
    report.initial_record_returned_at = datetime.utcnow()
    report.initial_record_return_note = note.strip() or None
    report.status = ComplaintReportStatus.returned
    _write_log(db, report, user, "初次處理紀錄退件", note)
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/final-result")
def complaint_final_result(
    report_id: str,
    content: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if not report.initial_record_approved_at:
        raise HTTPException(400, "初次處理紀錄尚未核章，不能送出最終處理或結案紀錄")
    if report.final_result_submitted_at and not report.final_result_approved_at and not report.final_result_returned_at:
        raise HTTPException(400, "處理結果已送出待核，退件前不能重送")
    report.final_result_content = content.strip()
    report.final_result_submitted_at = datetime.utcnow()
    report.final_result_submitted_by = user.id
    report.final_result_approved_at = None
    report.final_result_approved_by = None
    report.final_result_returned_at = None
    report.final_result_return_note = None
    report.status = ComplaintReportStatus.final_pending
    _write_log(db, report, user, "最終處理結果送出陳核")
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/final-result/approve")
def complaint_final_result_approve(
    report_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if not can_user_approve_complaint_stage(db, report, "final", user):
        raise HTTPException(403, "目前不是此處理結果的核章角色")
    approval_label = complaint_stage_approval_label("final", user.role)
    _write_log(db, report, user, approval_label, note.strip() or None)
    if not is_final_role_for_stage(db, report, "final", user.role):
        db.commit()
        return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)
    report.final_result_approved_at = datetime.utcnow()
    report.final_result_approved_by = user.id
    if report.report_kind == ComplaintReportKind.sexual_assault:
        report.closed_at = datetime.utcnow()
        report.status = ComplaintReportStatus.closed
    else:
        report.status = ComplaintReportStatus.in_review
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/final-result/return")
def complaint_final_result_return(
    report_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if not can_user_return_complaint_stage(db, report, "final", user):
        raise HTTPException(403, "目前不是此處理結果的退件角色")
    report.final_result_returned_at = datetime.utcnow()
    report.final_result_return_note = note.strip() or None
    report.status = ComplaintReportStatus.final_returned
    _write_log(db, report, user, "最終處理結果退回", note)
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/reply")
def complaint_reply(
    report_id: str,
    content: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report) or not report.final_result_approved_at:
        raise HTTPException(403, "權限不足")
    if report.reply_submitted_at and not report.reply_approved_at and not report.reply_returned_at:
        raise HTTPException(400, "回覆紀錄已送出待核，退件前不能重送")
    report.reply_content = content.strip()
    report.reply_submitted_at = datetime.utcnow()
    report.reply_submitted_by = user.id
    report.reply_approved_at = None
    report.reply_approved_by = None
    report.reply_returned_at = None
    report.reply_return_note = None
    report.status = ComplaintReportStatus.reply_pending
    _write_log(db, report, user, "回覆紀錄送出陳核")
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/reply/approve")
def complaint_reply_approve(
    report_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if not can_user_approve_complaint_stage(db, report, "reply", user):
        raise HTTPException(403, "目前不是此回覆紀錄的核章角色")
    approval_label = complaint_stage_approval_label("reply", user.role)
    _write_log(db, report, user, approval_label, note.strip() or None)
    if not is_final_role_for_stage(db, report, "reply", user.role):
        db.commit()
        return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)
    report.reply_approved_at = datetime.utcnow()
    report.reply_approved_by = user.id
    report.closed_at = datetime.utcnow() if report.report_kind != ComplaintReportKind.sexual_assault else datetime.utcnow()
    report.status = ComplaintReportStatus.replied
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)


@router.post("/{report_id}/reply/return")
def complaint_reply_return(
    report_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    report = db.query(ComplaintReport).filter(ComplaintReport.id == report_id).first()
    if not report or not _can_handle_report(user, report):
        raise HTTPException(403, "權限不足")
    if not can_user_return_complaint_stage(db, report, "reply", user):
        raise HTTPException(403, "目前不是此回覆紀錄的退件角色")
    report.reply_returned_at = datetime.utcnow()
    report.reply_return_note = note.strip() or None
    report.status = ComplaintReportStatus.reply_returned
    _write_log(db, report, user, "回覆紀錄退回", note)
    db.commit()
    return RedirectResponse(url=f"/complaints/{report.id}", status_code=302)
