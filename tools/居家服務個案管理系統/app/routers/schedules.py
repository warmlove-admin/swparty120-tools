import calendar
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.case import Case, CaseStatus
from app.models.service_schedule import ServiceSchedule
from app.models.user import User, UserRole

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
WEEKDAY_LABELS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def _parse_month(value: Optional[str]) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1) if value else date.today().replace(day=1)
    except ValueError:
        return date.today().replace(day=1)


def _month_shift(month: date, offset: int) -> date:
    month_number = month.month + offset
    return date(month.year + (month_number - 1) // 12, (month_number - 1) % 12 + 1, 1)


def _calendar_item(schedule: ServiceSchedule, service_date: date) -> dict:
    start_at = datetime.combine(service_date, schedule.start_time)
    end_at = start_at + timedelta(minutes=schedule.minutes)
    return {
        "schedule": schedule,
        "start_time": start_at.strftime("%H:%M"),
        "end_time": end_at.strftime("%H:%M"),
        "quantity": 1,
    }


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "找不到個案")
    return case


def _form_context(case: Case, user: User, schedule=None, error=None):
    return {
        "case": case, "user": user, "schedule": schedule, "error": error,
        "plans": [plan for plan in case.care_plans if plan.assigned_caregiver_id],
        "weekday_labels": list(enumerate(WEEKDAY_LABELS)),
    }


def _selected_item(case: Case, selection: str):
    try:
        plan_id, index = selection.split(":", 1)
        index = int(index)
    except (ValueError, AttributeError):
        return None, None
    plan = next((plan for plan in case.care_plans if plan.id == plan_id), None)
    if not plan or not plan.assigned_caregiver_id or index < 0 or index >= len(plan.coded_services):
        return None, None
    return plan, plan.coded_services[index]


async def _save(case: Case, request: Request, db: Session, schedule=None):
    form = await request.form()
    plan, item = _selected_item(case, form.get("plan_item", ""))
    try:
        weekdays = sorted({int(value) for value in form.getlist("weekdays")})
        start_time = datetime.strptime(form.get("start_time", ""), "%H:%M").time()
        effective_from = date.fromisoformat(form.get("effective_from", ""))
        until_value = form.get("effective_until", "")
        effective_until = date.fromisoformat(until_value) if until_value else None
    except ValueError:
        return None, "請填寫正確的服務日與開始時間。"
    if not plan or not item:
        return None, "請選擇已指派居服員的照顧計畫服務項目。"
    if not weekdays:
        return None, "請至少選擇一個服務日。"
    if effective_until and effective_until < effective_from:
        return None, "結束日期不可早於開始日期。"
    if len(weekdays) != int(item.get("quantity", 1)):
        return None, f"{item['code']} 的照顧計畫頻率為每週 {item.get('quantity', 1)} 次，請選擇相同數量的服務日。"
    values = {
        "care_plan_id": plan.id, "caregiver_id": plan.assigned_caregiver_id,
        "service_code": item["code"], "service_name": item["name"],
        "minutes": int(item["minutes_per_unit"]), "weekdays": weekdays,
        "start_time": start_time, "effective_from": effective_from,
        "effective_until": effective_until, "note": form.get("note") or None,
    }
    if schedule is None:
        schedule = ServiceSchedule(case_id=case.id, **values)
        db.add(schedule)
    else:
        for key, value in values.items():
            setattr(schedule, key, value)
    db.commit()
    return schedule, None


@router.get("/schedules", response_class=HTMLResponse)
def monthly_schedule(
    request: Request,
    month: Optional[str] = None,
    view: str = "all",
    caregiver_id: Optional[str] = None,
    case_id: Optional[str] = None,
    show_names: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    current_month = _parse_month(month)
    month_end = _month_shift(current_month, 1) - timedelta(days=1)
    query = db.query(ServiceSchedule).options(joinedload(ServiceSchedule.case), joinedload(ServiceSchedule.caregiver))
    if user.role == UserRole.caregiver:
        query = query.filter(ServiceSchedule.caregiver_id == user.id)
        caregiver_id = user.id
        view = "caregiver"
    elif view not in {"all", "caregiver", "case"}:
        view = "all"

    caregivers = db.query(User).filter(User.role == UserRole.caregiver, User.is_active.is_(True)).order_by(User.display_name).all()
    case_query = db.query(Case).join(ServiceSchedule).distinct().order_by(Case.name)
    if user.role == UserRole.caregiver:
        case_query = case_query.filter(ServiceSchedule.caregiver_id == user.id)
    cases = case_query.all()

    if view == "caregiver" and caregiver_id:
        query = query.filter(ServiceSchedule.caregiver_id == caregiver_id)
    if view == "case" and case_id:
        query = query.filter(ServiceSchedule.case_id == case_id)
    schedules = query.filter(ServiceSchedule.effective_from <= month_end, or_(ServiceSchedule.effective_until.is_(None), ServiceSchedule.effective_until >= current_month)).all()
    _, days = calendar.monthrange(current_month.year, current_month.month)
    first_weekday = current_month.weekday()
    cells = [{"day": None, "schedules": []} for _ in range(first_weekday)]
    for day in range(1, days + 1):
        current = date(current_month.year, current_month.month, day)
        items = [item for item in schedules if current.weekday() in item.weekdays and current >= item.effective_from and (not item.effective_until or current <= item.effective_until)]
        cells.append({"day": day, "schedules": [_calendar_item(item, current) for item in sorted(items, key=lambda item: item.start_time)]})
    while len(cells) % 7:
        cells.append({"day": None, "schedules": []})
    return templates.TemplateResponse(request, "schedule_calendar.html", {
        "user": user, "current_month": current_month,
        "previous_month": _month_shift(current_month, -1), "next_month": _month_shift(current_month, 1),
        "weeks": [cells[i:i + 7] for i in range(0, len(cells), 7)], "weekday_labels": WEEKDAY_LABELS,
        "view": view, "caregiver_id": caregiver_id, "case_id": case_id,
        "caregivers": caregivers, "cases": cases, "show_names": show_names,
    })


@router.get("/cases/{case_id}/schedules/new", response_class=HTMLResponse)
def new_form(case_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    case = _get_case_or_404(db, case_id)
    if case.status != CaseStatus.active:
        raise HTTPException(400, "僅可為服務中的個案建立班表")
    return templates.TemplateResponse(request, "schedule_form.html", _form_context(case, user))


@router.post("/cases/{case_id}/schedules")
async def create(case_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    case = _get_case_or_404(db, case_id)
    _, error = await _save(case, request, db)
    if error:
        return templates.TemplateResponse(request, "schedule_form.html", _form_context(case, user, error=error), status_code=400)
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.get("/cases/{case_id}/schedules/{schedule_id}/edit", response_class=HTMLResponse)
def edit_form(case_id: str, schedule_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    case = _get_case_or_404(db, case_id)
    schedule = db.query(ServiceSchedule).filter_by(id=schedule_id, case_id=case.id).first()
    if not schedule:
        raise HTTPException(404, "找不到服務班表")
    return templates.TemplateResponse(request, "schedule_form.html", _form_context(case, user, schedule=schedule))


@router.post("/cases/{case_id}/schedules/{schedule_id}/edit")
async def edit(case_id: str, schedule_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    case = _get_case_or_404(db, case_id)
    schedule = db.query(ServiceSchedule).filter_by(id=schedule_id, case_id=case.id).first()
    if not schedule:
        raise HTTPException(404, "找不到服務班表")
    _, error = await _save(case, request, db, schedule)
    if error:
        return templates.TemplateResponse(request, "schedule_form.html", _form_context(case, user, schedule=schedule, error=error), status_code=400)
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.post("/cases/{case_id}/schedules/{schedule_id}/delete")
def delete(case_id: str, schedule_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    schedule = db.query(ServiceSchedule).filter_by(id=schedule_id, case_id=case_id).first()
    if not schedule:
        raise HTTPException(404, "找不到服務班表")
    db.delete(schedule)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}", status_code=302)
