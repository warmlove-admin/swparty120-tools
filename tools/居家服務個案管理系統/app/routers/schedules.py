import calendar
import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import quote

import os
from fastapi import APIRouter, Depends, Form, HTTPException, Request
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
from app.services.excel_schedule_import import parse_directory, import_entries_to_db

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
    "ZA04": "服務未遇",
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


def _imported_calendar_item(record: CaregiverServiceRecord, view: str = "", caregiver_id: str = "", case_id: str = "", show_names: bool = True) -> dict:
    from app.services.ltc_code_catalog import parse_funding as _pf, get_code_quantity
    is_leave = record.formalization_status in ("leave",)
    label = "請假" if is_leave else STATUS_LABELS.get(record.formalization_status, "外部匯入")
    funding_map = _pf(record.service_codes, record.funding_source, record.funding_detail)
    raw_lines = _service_lines(record.service_codes)

    # Aggregate by code (e.g. BA20x1, BA20x6 → BA20 total=7)
    from collections import OrderedDict
    code_agg: OrderedDict[str, dict] = OrderedDict()
    for sl in raw_lines:
        code = sl["code"]
        name = sl["name"]
        if code in code_agg:
            code_agg[code]["qty"] += int(sl["quantity"])
        else:
            code_agg[code] = {"code": code, "name": name, "qty": int(sl["quantity"])}

    service_lines = []
    for code, info in code_agg.items():
        total_qty = info["qty"]
        instance_keys = sorted(k for k in funding_map if k.startswith(f"{code}."))
        if instance_keys:
            # Group per-instance keys by funding value
            groups: dict[str, int] = {}
            for ik in instance_keys:
                fv = funding_map[ik]
                groups[fv] = groups.get(fv, 0) + 1
            for fv, qty in sorted(groups.items(), key=lambda x: -x[1] if x[0] == record.funding_source else 0):
                service_lines.append({"code": code, "quantity": str(qty), "name": info["name"], "funding": fv})
        else:
            fv = funding_map.get(code, record.funding_source)
            service_lines.append({"code": code, "quantity": str(total_qty), "name": info["name"], "funding": fv})

    back_params = {"month": record.service_date.strftime('%Y-%m'), "show_names": str(show_names).lower()}
    if view:
        back_params["view"] = view
    if caregiver_id:
        back_params["caregiver_id"] = caregiver_id
    if case_id:
        back_params["case_id"] = case_id
    back_qs = "&".join(f"{k}={quote(v)}" for k, v in back_params.items())

    return {
        "kind": "imported",
        "record": record,
        "case": record.case,
        "caregiver": record.caregiver,
        "start_time": record.start_time.strftime("%H:%M"),
        "end_time": record.end_time.strftime("%H:%M"),
        "service_code": record.service_codes or "-",
        "service_name": "",
        "service_lines": service_lines,
        "quantity": 1,
        "status_label": label,
        "is_leave": is_leave,
        "case_id": record.case_id,
        "href": f"/imported-records/{record.id}/edit?back={quote('/schedules?' + back_qs)}",
    }


def _check_weekend_schedule_warning(schedule: ServiceSchedule, db: Session) -> str | None:
    """Check if caregiver has both Sat and Sun schedules but monthly hours < 172."""
    from app.services.attendance_engine import _get_month_hours
    from sqlalchemy import func as sa_func
    caregiver_id = schedule.caregiver_id
    if not caregiver_id:
        return None
    active_schedules = db.query(ServiceSchedule).filter(
        ServiceSchedule.caregiver_id == caregiver_id,
        ServiceSchedule.effective_from <= date.today(),
        sa_func.coalesce(ServiceSchedule.effective_until, date(9999, 12, 31)) >= date.today(),
    ).all()
    all_weekdays = set()
    for s in active_schedules:
        all_weekdays.update(s.weekdays)
    has_both_weekend = 5 in all_weekdays and 6 in all_weekdays
    if not has_both_weekend:
        return None
    today = date.today()
    monthly_hours = _get_month_hours(caregiver_id, today.year, today.month, db)
    if monthly_hours >= 172.0:
        return None
    return (
        f"⚠️ {schedule.caregiver.display_name} 目前已排定六日皆有班表，"
        f"但本月({today.year}/{today.month})總服務時數 {monthly_hours:.0f}h "
        f"未達彈性工時門檻 172h。請注意休息日／例假日之出勤規範。"
    )


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "找不到個案")
    return case


def _get_adjacent_schedules(caregiver_id: str, case_id: str, weekday: int, start_time, exclude_id: str | None, db: Session) -> dict:
    """Find schedules before and after this case for the same caregiver on a given weekday."""
    others = db.query(ServiceSchedule).filter(
        ServiceSchedule.caregiver_id == caregiver_id,
        ServiceSchedule.id != exclude_id,
    ).all()
    same_day = [s for s in others if weekday in s.weekdays]
    same_day.sort(key=lambda s: (s.start_time, s.case.name if s.case else ""))
    prev_s = next_s = None
    for i, s in enumerate(same_day):
        if s.start_time >= start_time:
            prev_s = same_day[i - 1] if i > 0 else None
            next_s = s
            break
    else:
        prev_s = same_day[-1] if same_day else None
    return {"prev": prev_s, "next": next_s, "all": same_day}


def _route_summary(case: Case, schedule, caregiver_name: str, prev_case_name: str, next_case_name: str) -> str:
    parts = []
    if case.dialysis_direction in ("接", "送+接") and prev_case_name:
        parts.append(f"前一段：{prev_case_name} → {case.name}，轉場終點改為醫院")
    if case.dialysis_direction in ("送", "送+接") and next_case_name:
        parts.append(f"後一段：{case.name} → {next_case_name}，轉場起點改為醫院")
    if not parts:
        parts.append("此個案為洗腎案，但未影響轉場路線（無前後案或方向未設定）")
    return "；".join(parts)


def _need_dialysis_confirmation(case: Case, service_code: str) -> bool:
    return case.is_dialysis == "Y" and "BA13" in service_code.upper()


def _dialysis_adjacent_context(case: Case, schedule, db: Session):
    """Build route confirmation context for a dialysis case's schedule."""
    if not schedule or not _need_dialysis_confirmation(case, schedule.service_code):
        return {}
    caregiver = schedule.caregiver
    caregiver_name = caregiver.display_name if caregiver else "?"
    adj_by_weekday = {}
    for wd in schedule.weekdays:
        adj = _get_adjacent_schedules(schedule.caregiver_id, case.id, wd, schedule.start_time, schedule.id, db)
        prev_name = adj["prev"].case.name if adj["prev"] and adj["prev"].case else None
        next_name = adj["next"].case.name if adj["next"] and adj["next"].case else None
        adj_by_weekday[wd] = {"prev_name": prev_name, "next_name": next_name, "route": _route_summary(case, schedule, caregiver_name, prev_name, next_name)}
    weekday_labels = WEEKDAY_LABELS
    return {
        "dialysis_case": case,
        "dialysis_caregiver_name": caregiver_name,
        "dialysis_adjacent": adj_by_weekday,
        "weekday_labels": weekday_labels,
    }


def _form_context(case: Case, user: User, schedule=None, error=None, db=None):
    ctx = {
        "case": case, "user": user, "schedule": schedule, "error": error,
        "plans": [plan for plan in case.care_plans if plan.assigned_caregiver_id],
        "weekday_labels": list(enumerate(WEEKDAY_LABELS)),
    }
    if db and schedule and _need_dialysis_confirmation(case, schedule.service_code):
        ctx.update(_dialysis_adjacent_context(case, schedule, db))
    return ctx


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
        "effective_until": effective_until,
        "funding_source": form.get("funding_source") or item.get("funding_source", "補助"),
        "note": form.get("note") or None,
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

    today = date.today()
    caregivers = (
        db.query(User)
        .filter(
            User.role == UserRole.caregiver,
            User.is_active.is_(True),
            (User.termination_date.is_(None)) | (User.termination_date > today),
        )
        .order_by(User.display_name)
        .all()
    )
    case_by_id = {}
    case_query = db.query(Case).join(ServiceSchedule).distinct().filter(Case.status != CaseStatus.closed)
    if user.role == UserRole.caregiver:
        case_query = case_query.filter(ServiceSchedule.caregiver_id == user.id)
    for case in case_query.all():
        case_by_id[case.id] = case
    imported_case_query = db.query(Case).join(CaregiverServiceRecord).distinct().filter(Case.status != CaseStatus.closed)
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
            _imported_calendar_item(record, view, caregiver_id, case_id, show_names)
            for record in imported_records
            if record.service_date == current
        ]
        items = sorted(schedule_items + imported_items, key=lambda item: (item["start_time"], item["case"].name if item["case"] else ""))
        cells.append({"day": day, "schedules": items})
    while len(cells) % 7:
        cells.append({"day": None, "schedules": []})
    current_case_name = ""
    if case_id and case_id in case_by_id:
        current_case_name = case_by_id[case_id].name
    return templates.TemplateResponse(request, "schedule_calendar.html", {
        "user": user, "current_month": current_month,
        "previous_month": _month_shift(current_month, -1), "next_month": _month_shift(current_month, 1),
        "weeks": [cells[i:i + 7] for i in range(0, len(cells), 7)], "weekday_labels": WEEKDAY_LABELS,
        "view": view, "caregiver_id": caregiver_id, "case_id": case_id,
        "caregivers": caregivers, "cases": cases, "show_names": show_names,
        "imported_count": len(imported_records),
        "formal_count": len(schedules),
        "current_case_name": current_case_name,
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
    schedule, error = await _save(case, request, db)
    if error:
        return templates.TemplateResponse(request, "schedule_form.html", _form_context(case, user, error=error), status_code=400)
    # 洗腎接送路線確認
    form = await request.form()
    if schedule and _need_dialysis_confirmation(case, schedule.service_code) and form.get("dialysis_confirmed") != "yes":
        ctx = _form_context(case, user, schedule=schedule, db=db, error="請確認下方洗腎接送路線後打勾再儲存。")
        ctx["dialysis_needs_confirmation"] = True
        return templates.TemplateResponse(request, "schedule_form.html", ctx, status_code=400)
    warning = _check_weekend_schedule_warning(schedule, db)
    redirect_url = f"/cases/{case.id}"
    if warning:
        redirect_url += f"?schedule_warning={quote(warning)}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/cases/{case_id}/schedules/{schedule_id}/edit", response_class=HTMLResponse)
def edit_form(case_id: str, schedule_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    case = _get_case_or_404(db, case_id)
    schedule = db.query(ServiceSchedule).filter_by(id=schedule_id, case_id=case.id).first()
    if not schedule:
        raise HTTPException(404, "找不到服務班表")
    return templates.TemplateResponse(request, "schedule_form.html", _form_context(case, user, schedule=schedule, db=db))


@router.post("/cases/{case_id}/schedules/{schedule_id}/edit")
async def edit(case_id: str, schedule_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    case = _get_case_or_404(db, case_id)
    schedule = db.query(ServiceSchedule).filter_by(id=schedule_id, case_id=case.id).first()
    if not schedule:
        raise HTTPException(404, "找不到服務班表")
    schedule_obj, error = await _save(case, request, db, schedule)
    if error:
        return templates.TemplateResponse(request, "schedule_form.html", _form_context(case, user, schedule=schedule, db=db, error=error), status_code=400)
    # 洗腎接送路線確認
    form = await request.form()
    if _need_dialysis_confirmation(case, schedule.service_code) and form.get("dialysis_confirmed") != "yes":
        ctx = _form_context(case, user, schedule=schedule, db=db, error="請確認下方洗腎接送路線後打勾再儲存。")
        ctx["dialysis_needs_confirmation"] = True
        return templates.TemplateResponse(request, "schedule_form.html", ctx, status_code=400)
    warning = _check_weekend_schedule_warning(schedule_obj or schedule, db)
    redirect_url = f"/cases/{case.id}"
    if warning:
        redirect_url += f"?schedule_warning={quote(warning)}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/import-schedules")
def import_schedules_page(
    request: Request,
    month: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    from app.services.excel_schedule_import import parse_directory as _parse_dir
    return templates.TemplateResponse(request, "import_schedules.html", {
        "user": user, "month": month or date.today().strftime("%Y-%m"),
    })


@router.post("/import-schedules/run")
def import_schedules_run(
    month: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    current_month = _parse_month(month)
    download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "已執行班表6月")
    if not os.path.isdir(download_dir):
        return RedirectResponse(
            url=f"/schedules?month={month}&error=找不到匯入資料夾：{download_dir}",
            status_code=302,
        )
    all_entries = parse_directory(download_dir, current_month.year)
    total = sum(len(v) for v in all_entries.values())
    result = import_entries_to_db(
        [e for entries in all_entries.values() for e in entries],
        current_month, db,
    )
    return RedirectResponse(
        url=f"/schedules?month={month}&success=匯入完成：已新增 {result['added']} 筆（{len(all_entries)} 位居服員），未匹配個案 {result['skipped_no_case']}，未匹配居服員 {result['skipped_no_cg']}",
        status_code=302,
    )


@router.get("/imported-records/{record_id}/edit")
def edit_imported_record(
    record_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    import re, collections
    from app.services.ltc_code_catalog import parse_funding as _parse_funding
    record = db.query(CaregiverServiceRecord).filter(CaregiverServiceRecord.id == record_id).first()
    if not record:
        raise HTTPException(404, "找不到匯入記錄")
    back = request.query_params.get("back", f"/schedules?month={record.service_date.strftime('%Y-%m')}")
    funding_map = _parse_funding(record.service_codes, record.funding_source, record.funding_detail)

    # Build per-code totals and 補/自 counts
    code_totals: dict[str, int] = {}
    if record.service_codes:
        for m in re.finditer(r"([A-Z]{1,3}\d{1,2}(?:-\d+)?(?:[a-z]\d?)?)\s*x\s*(\d+)", record.service_codes, re.IGNORECASE):
            c = m.group(1).upper()
            code_totals[c] = code_totals.get(c, 0) + int(m.group(2))

    code_funding: dict[str, dict[str, int]] = {}
    for code in code_totals:
        cnt: dict[str, int] = {"補助": 0, "自費": 0}
        # Check for per-instance keys
        for k, v in funding_map.items():
            if k == code or k.startswith(f"{code}."):
                cnt[v] = cnt.get(v, 0) + 1
        if sum(cnt.values()) == 0:
            cnt[funding_map.get(code, record.funding_source)] = code_totals[code]
        code_funding[code] = cnt

    return templates.TemplateResponse(request, "edit_imported_record.html", {
        "user": user, "record": record, "back": back,
        "code_totals": code_totals, "code_funding": code_funding,
    })


@router.post("/imported-records/{record_id}/edit")
async def save_imported_record(
    record_id: str,
    request: Request,
    funding_source: str = Form(...),
    formalization_status: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    import json, re

    record = db.query(CaregiverServiceRecord).filter(CaregiverServiceRecord.id == record_id).first()
    if not record:
        raise HTTPException(404, "找不到匯入記錄")
    record.formalization_status = formalization_status

    form = dict(await request.form())
    funding_per_code: dict[str, str | list] = {}

    # 解析所有碼別及總量
    code_total: dict[str, int] = {}
    if record.service_codes:
        for m in re.finditer(r"([A-Z]{1,3}\d{1,2}(?:-\d+)?(?:[a-z]\d?)?)\s*x\s*(\d+)", record.service_codes, re.IGNORECASE):
            c = m.group(1).upper()
            code_total[c] = code_total.get(c, 0) + int(m.group(2))

    for code, total_qty in code_total.items():
        if total_qty > 1:
            # 從數字輸入框取得 補/自 數量
            sub_key = f"funding_{code}_補"
            self_key = f"funding_{code}_自"
            try:
                sub_cnt = int(form.get(sub_key, 0))
                self_cnt = int(form.get(self_key, 0))
            except (ValueError, TypeError):
                sub_cnt = 0
                self_cnt = 0
            if sub_cnt + self_cnt != total_qty:
                sub_cnt = total_qty
                self_cnt = 0
            groups = []
            if sub_cnt > 0 or self_cnt == 0:
                groups.append({"funding": "補助", "qty": sub_cnt})
            if self_cnt > 0:
                groups.append({"funding": "自費", "qty": self_cnt})
            # 如果全部同一類型，用字串簡化
            if len(groups) == 1:
                val = groups[0]["funding"]
                if val != funding_source:
                    funding_per_code[code] = val
            else:
                funding_per_code[code] = groups
        else:
            # 單一數量：從 radio 取值
            key = f"funding_{code}"
            val = form.get(key, "")
            if val in ("補助", "自費") and val != funding_source:
                funding_per_code[code] = val

    record.funding_source = funding_source
    if funding_per_code:
        record.funding_detail = json.dumps(funding_per_code, ensure_ascii=False)
    else:
        record.funding_detail = None
    db.commit()
    back = request.query_params.get("back", f"/schedules?month={record.service_date.strftime('%Y-%m')}")
    return RedirectResponse(url=back, status_code=302)


@router.post("/cases/{case_id}/schedules/{schedule_id}/delete")
def delete(case_id: str, schedule_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager))):
    schedule = db.query(ServiceSchedule).filter_by(id=schedule_id, case_id=case_id).first()
    if not schedule:
        raise HTTPException(404, "找不到服務班表")
    db.delete(schedule)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}", status_code=302)
