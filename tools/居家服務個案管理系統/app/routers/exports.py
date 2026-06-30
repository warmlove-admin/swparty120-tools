import calendar
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Integer, cast, desc, func
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.database import get_db
from app.models.case import Case, CaseStatus
from app.models.complaint_report import ComplaintReport, ComplaintReportKind, ComplaintReportStatus
from app.models.contact_record import ContactRecord
from app.models.record_status_log import RecordStatusLog
from app.models.user import User, UserRole
from app.routers.cases import visible_cases_query
from app.services.complaint_workflow import complaint_stage_signature_rows
from app.services.signature_stamps import contact_review_rows

router = APIRouter(prefix="/exports")
templates = Jinja2Templates(directory="app/templates")


def _parse_month_date(value: str, fallback: date) -> date:
    if not value:
        return fallback
    if len(value) == 7:
        value = f"{value}-01"
    return date.fromisoformat(value).replace(day=1)


def _parse_plain_date(value: str, fallback: date) -> date:
    if not value:
        return fallback
    return date.fromisoformat(value)


def _month_shift(value: date, offset: int) -> date:
    month_number = value.month + offset
    return date(value.year + (month_number - 1) // 12, (month_number - 1) % 12 + 1, 1)


def _month_range(start_month: date, end_month: date) -> list[date]:
    months = []
    current = start_month
    while current <= end_month:
        months.append(current)
        current = _month_shift(current, 1)
    return months


def _roc_month_label(value: date) -> str:
    return f"{value.year - 1911}年{value.month}月"


def _case_sort_key(case: Case) -> tuple[int, int, str]:
    org_case_no = case.org_case_no or ""
    if org_case_no.startswith("XLSROW"):
        return (1, 0, case.name)
    if org_case_no.isdigit():
        return (0, -int(org_case_no), case.name)
    return (1, 0, case.name)


def _status_options() -> list[dict]:
    return [
        {"value": "active", "label": "服務中", "status": CaseStatus.active},
        {"value": "paused", "label": "暫停中", "status": CaseStatus.paused},
        {"value": "closed", "label": "已結案", "status": CaseStatus.closed},
        {"value": "all", "label": "全部個案", "status": None},
    ]


def _complaint_status_options() -> list[dict]:
    return [
        {"value": "", "label": "全部狀態", "status": None},
        {"value": ComplaintReportStatus.submitted.value, "label": ComplaintReportStatus.submitted.value, "status": ComplaintReportStatus.submitted},
        {"value": ComplaintReportStatus.in_review.value, "label": ComplaintReportStatus.in_review.value, "status": ComplaintReportStatus.in_review},
        {"value": ComplaintReportStatus.final_pending.value, "label": ComplaintReportStatus.final_pending.value, "status": ComplaintReportStatus.final_pending},
        {"value": ComplaintReportStatus.reply_pending.value, "label": ComplaintReportStatus.reply_pending.value, "status": ComplaintReportStatus.reply_pending},
        {"value": ComplaintReportStatus.replied.value, "label": ComplaintReportStatus.replied.value, "status": ComplaintReportStatus.replied},
        {"value": ComplaintReportStatus.closed.value, "label": ComplaintReportStatus.closed.value, "status": ComplaintReportStatus.closed},
    ]


def _cases_for_filter(db: Session, user: User, status_filter: str) -> list[Case]:
    status_by_value = {option["value"]: option["status"] for option in _status_options()}
    if status_filter not in status_by_value:
        status_filter = "active"
    query = visible_cases_query(db, user)
    if status_by_value[status_filter] is not None:
        query = query.filter(Case.status == status_by_value[status_filter])
    return sorted(query.all(), key=_case_sort_key)


@router.get("", response_class=HTMLResponse)
def export_index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    today = date.today()
    default_start = date(today.year, 1, 1)
    default_end = date(today.year, today.month, 1)
    next_month = _month_shift(default_end, 1)
    employee_no_number = cast(func.substr(User.employee_no, 2), Integer)
    caregivers = (
        db.query(User)
        .filter(User.role == UserRole.caregiver, User.is_active.is_(True))
        .order_by(User.employee_no.is_(None), desc(employee_no_number), User.display_name)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "exports_index.html",
        {
            "user": user,
            "active_cases": _cases_for_filter(db, user, "active"),
            "caregivers": caregivers,
            "status_options": _status_options(),
            "complaint_kinds": list(ComplaintReportKind),
            "complaint_status_options": _complaint_status_options(),
            "default_start": default_start,
            "default_end": default_end,
            "next_month": next_month,
            "today": today,
            "roc_month_label": _roc_month_label,
        },
    )


@router.get("/contact-records/print", response_class=HTMLResponse)
def print_contact_records_batch(
    request: Request,
    start: str = "",
    end: str = "",
    status_filter: str = "active",
    case_id: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    today = date.today()
    default_start = date(today.year, 1, 1)
    default_end = date(today.year, today.month, 1)
    start_month = _parse_month_date(start, default_start)
    end_month = _parse_month_date(end, default_end)
    if end_month < start_month:
        start_month, end_month = end_month, start_month

    cases = _cases_for_filter(db, user, status_filter)
    if case_id:
        cases = [case for case in cases if case.id == case_id]
    months = _month_range(start_month, end_month)
    records_by_case_month = {case.id: {month: [] for month in months} for case in cases}

    if cases:
        range_end = _month_shift(end_month, 1)
        records = (
            db.query(ContactRecord)
            .filter(
                ContactRecord.case_id.in_([case.id for case in cases]),
                ContactRecord.contact_date >= start_month,
                ContactRecord.contact_date < range_end,
            )
            .order_by(ContactRecord.case_id.asc(), ContactRecord.contact_date.asc(), ContactRecord.created_at.asc())
            .all()
        )
        for record in records:
            month = record.contact_date.replace(day=1)
            records_by_case_month.setdefault(record.case_id, {}).setdefault(month, []).append(record)
    else:
        records = []

    review_logs = (
        db.query(RecordStatusLog)
        .filter(
            RecordStatusLog.record_type == "contact_record",
            RecordStatusLog.record_id.in_([record.id for record in records]),
        )
        .order_by(RecordStatusLog.created_at.asc())
        .all()
    ) if records else []
    review_rows_by_record = {}
    for log in review_logs:
        review_rows_by_record.setdefault(log.record_id, []).append(log)
    review_rows_by_record = {
        record_id: contact_review_rows(logs)
        for record_id, logs in review_rows_by_record.items()
    }

    return templates.TemplateResponse(
        request,
        "contact_records_batch_print.html",
        {
            "user": user,
            "cases": cases,
            "status_options": _status_options(),
            "status_filter": status_filter,
            "selected_case_id": case_id,
            "start_month": start_month,
            "end_month": end_month,
            "months": months,
            "records_by_case_month": records_by_case_month,
            "review_rows_by_record": review_rows_by_record,
            "calendar": calendar,
            "roc_month_label": _roc_month_label,
        },
    )


@router.get("/service-records/print", response_class=HTMLResponse)
def print_service_records_placeholder(month: str = "", status_filter: str = "active"):
    target = month or date.today().strftime("%Y-%m")
    return RedirectResponse(url=f"/exports?service_record_month={target}&status_filter={status_filter}", status_code=302)


@router.get("/complaints/print", response_class=HTMLResponse)
def print_complaint_reports(
    request: Request,
    kind: str = "",
    start: str = "",
    end: str = "",
    status_filter: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    today = date.today()
    default_start = date(today.year, 1, 1)
    default_end = today
    start_date = _parse_plain_date(start, default_start)
    end_date = _parse_plain_date(end, default_end)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    selected_kind = ComplaintReportKind.general
    if kind:
        selected_kind = ComplaintReportKind(kind)

    query = (
        db.query(ComplaintReport)
        .filter(
            ComplaintReport.report_kind == selected_kind,
            ComplaintReport.received_date >= start_date,
            ComplaintReport.received_date <= end_date,
        )
        .order_by(ComplaintReport.received_date.asc(), ComplaintReport.submitted_at.asc())
    )
    if status_filter:
        query = query.filter(ComplaintReport.status == ComplaintReportStatus(status_filter))
    reports = query.all()

    return templates.TemplateResponse(
        request,
        "complaint_reports_print.html",
        {
            "user": user,
            "reports": reports,
            "selected_kind": selected_kind,
            "start_date": start_date,
            "end_date": end_date,
            "status_filter": status_filter,
            "today": today,
            "complaint_stage_signature_rows": complaint_stage_signature_rows,
            "db": db,
        },
    )
