import calendar
import re
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
from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.service_schedule import ServiceSchedule
from app.models.user import User, UserRole
from app.services.ltc_code_catalog import CODE_LOOKUP
from app.services.schedule_formalization import STATUS_LABELS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
WEEKDAY_LABELS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
SERVICE_TOKEN_RE = re.compile(r"([A-Z]{1,3}\d{1,2}(?:-\d+)?(?:(?!x)[a-z]\d?)?)(?:\s*x\s*(\d+))?", re.IGNORECASE)
SHORT_SERVICE_NAMES = {
    "BA16-1": "代購代領",
    "BA16-2": "代購代領",
    "BA07": "沐浴洗頭",
    "BA02": "日常照顧",
    "BA12": "上下樓梯",
    "BA17d1": "驗血糖",
    "BA05-1": "一般備餐",
    "BA20": "陪伴服務",
    "BA04": "管灌餵食",
    "BA15-1": "家務自用",
    "BA15-2": "家務共用",
    "BA03": "生命徵象",
    "BA11": "肢關活動",
}


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
        "kind": "formal",
        "schedule": schedule,
        "case": schedule.case,
        "caregiver": schedule.caregiver,
        "start_time": start_at.strftime("%H:%M"),
        "end_time": end_at.strftime("%H:%M"),
        "service_code": schedule.service_code,
        "service_name": schedule.service_name,
        "service_lines": [{
            "code": schedule.service_code,
            "quantity": "1",
            "name": schedule.service_name,
        }],
        "quantity": 1,
        "status_label": "正式班表",
        "case_id": schedule.case_id,
        "href": f"/cases/{schedule.case_id}?tab=schedule",
    }


def _service_lines(raw_codes: str | None) -> list[dict]:
    lines = []
    for match in SERVICE_TOKEN_RE.finditer(raw_codes or ""):
        raw_code = match.group(1)
        code = raw_code.upper()
        lookup_code = raw_code if raw_code in CODE_LOOKUP else code
        if lookup_code not in CODE_LOOKUP and len(raw_code) > 4:
            lookup_code = raw_code[:4].upper() + raw_code[4:].lower()
        quantity = match.group(2) or "1"
        name = _short_service_name(lookup_code)
        lines.append({"code": lookup_code, "quantity": quantity, "name": name})
    if lines:
        return lines
    return [{"code": raw_codes or "-", "quantity": "", "name": ""}]


def _short_service_name(code: str) -> str:
    if code in SHORT_SERVICE_NAMES:
        return SHORT_SERVICE_NAMES[code]
    full_name = CODE_LOOKUP.get(code, ("", 0))[0]
    if not full_name:
        return ""
    cleaned = re.sub(r"[（）()－—、／/].*$", "", full_name)
    cleaned = cleaned.replace("協助", "").replace("服務", "").replace("基本", "")
    return cleaned[:4] if len(cleaned) > 4 else cleaned


def _imported_calendar_item(record: CaregiverServiceRecord) -> dict:
    return {
        "kind": "imported",
        "record": record,
        "case": record.case,
        "caregiver": record.caregiver,
        "start_time": record.start_time.strftime("%H:%M"),
        "end_time": record.end_time.strftime("%H:%M"),
        "service_code": record.service_codes or "-",
        "service_name": "",
        "service_lines": _service_lines(record.service_codes),
        "quantity": 1,
        "status_label": STATUS_LABELS.get(record.formalization_status, "外部匯入"),
        "case_id": record.case_id,
        "href": f"/cases/{record.case_id}?tab=schedule",
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
    imported_query = db.query(CaregiverServiceRecord).options(
        joinedload(CaregiverServiceRecord.case),
        joinedload(CaregiverServiceRecord.caregiver),
    )
    if user.role == UserRole.caregiver:
        query = query.filter(ServiceSchedule.caregiver_id == user.id)
        imported_query = imported_query.filter(CaregiverServiceRecord.caregiver_id == user.id)
        caregiver_id = user.id
        view = "caregiver"
    elif view not in {"all", "caregiver", "case"}:
        view = "all"

    caregivers = db.query(User).filter(User.role == UserRole.caregiver, User.is_active.is_(True)).order_by(User.display_name).all()
    case_by_id = {}
    case_query = db.query(Case).join(ServiceSchedule).distinct()
    if user.role == UserRole.caregiver:
        case_query = case_query.filter(ServiceSchedule.caregiver_id == user.id)
    for case in case_query.all():
        case_by_id[case.id] = case
    imported_case_query = db.query(Case).join(CaregiverServiceRecord).distinct()
    if user.role == UserRole.caregiver:
        imported_case_query = imported_case_query.filter(CaregiverServiceRecord.caregiver_id == user.id)
    for case in imported_case_query.all():
        case_by_id.setdefault(case.id, case)
    cases = sorted(case_by_id.values(), key=lambda case: (case.name, case.org_case_no))

    if view == "caregiver" and caregiver_id:
        query = query.filter(ServiceSchedule.caregiver_id == caregiver_id)
        imported_query = imported_query.filter(CaregiverServiceRecord.caregiver_id == caregiver_id)
    if view == "case" and case_id:
        query = query.filter(ServiceSchedule.case_id == case_id)
        imported_query = imported_query.filter(CaregiverServiceRecord.case_id == case_id)
    schedules = query.filter(ServiceSchedule.effective_from <= month_end, or_(ServiceSchedule.effective_until.is_(None), ServiceSchedule.effective_until >= current_month)).all()
    imported_records = imported_query.filter(
        CaregiverServiceRecord.service_date >= current_month,
        CaregiverServiceRecord.service_date <= month_end,
    ).all()
    _, days = calendar.monthrange(current_month.year, current_month.month)
    first_weekday = current_month.weekday()
    cells = [{"day": None, "schedules": []} for _ in range(first_weekday)]
    for day in range(1, days + 1):
        current = date(current_month.year, current_month.month, day)
        schedule_items = [
            _calendar_item(item, current)
            for item in schedules
            if current.weekday() in item.weekdays and current >= item.effective_from and (not item.effective_until or current <= item.effective_until)
        ]
        imported_items = [
            _imported_calendar_item(record)
            for record in imported_records
            if record.service_date == current
        ]
        items = sorted(schedule_items + imported_items, key=lambda item: (item["start_time"], item["case"].name if item["case"] else ""))
        cells.append({"day": day, "schedules": items})
    while len(cells) % 7:
        cells.append({"day": None, "schedules": []})
    return templates.TemplateResponse(request, "schedule_calendar.html", {
        "user": user, "current_month": current_month,
        "previous_month": _month_shift(current_month, -1), "next_month": _month_shift(current_month, 1),
        "weeks": [cells[i:i + 7] for i in range(0, len(cells), 7)], "weekday_labels": WEEKDAY_LABELS,
        "view": view, "caregiver_id": caregiver_id, "case_id": case_id,
        "caregivers": caregivers, "cases": cases, "show_names": show_names,
        "imported_count": len(imported_records),
        "formal_count": len(schedules),
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
