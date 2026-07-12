import calendar
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.national_holiday import NationalHoliday
from app.models.user import User, UserRole
from app.services.attendance_engine import (
    SUN_FIRST_LABELS,
    WEEKDAY_LABELS,
    DATE_TYPE_LABELS,
    DATE_TYPE_COLORS,
    OVERTIME_MULTIPLIERS,
    all_caregivers_calendar,
    classify_date_no_db,
    get_holiday_dates_for_year,
    month_calendar_data,
    seed_national_holidays,
)

router = APIRouter(prefix="/attendance")
templates = Jinja2Templates(directory="app/templates")


def _parse_month(value: Optional[str]) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1) if value else date.today().replace(day=1)
    except ValueError:
        return date.today().replace(day=1)


def _month_shift(month: date, offset: int) -> date:
    month_number = month.month + offset
    return date(month.year + (month_number - 1) // 12, (month_number - 1) % 12 + 1, 1)


@router.get("", response_class=HTMLResponse)
def attendance_index(
    request: Request,
    month: Optional[str] = None,
    caregiver_id: Optional[str] = None,
    view: str = "single",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    current_month = _parse_month(month)
    previous_month = _month_shift(current_month, -1)
    next_month = _month_shift(current_month, 1)
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
    selected_caregiver = None
    if caregiver_id:
        selected_caregiver = db.query(User).filter(User.id == caregiver_id).first()
    if not selected_caregiver and caregivers:
        selected_caregiver = caregivers[0]

    calendar_days = None
    overview_data = None
    days_in_month = 0
    if view == "overview":
        overview_data, days_in_month = all_caregivers_calendar(
            current_month.year, current_month.month, db
        )
    elif selected_caregiver:
        calendar_days = month_calendar_data(
            selected_caregiver, current_month.year, current_month.month, db
        )
        days_in_month = len(calendar_days)

    holiday_map = get_holiday_dates_for_year(db, current_month.year)
    holiday_objects = (
        db.query(NationalHoliday)
        .filter(NationalHoliday.year == current_month.year)
        .order_by(NationalHoliday.holiday_date)
        .all()
    )

    first_py_weekday = calendar.monthrange(current_month.year, current_month.month)[0]
    padding = [{"day": None}] * ((first_py_weekday + 1) % 7)

    return templates.TemplateResponse(
        request,
        "attendance_calendar.html",
        {
            "user": user,
            "current_month": current_month,
            "previous_month": previous_month,
            "next_month": next_month,
            "caregivers": caregivers,
            "selected_caregiver": selected_caregiver,
            "calendar_days": calendar_days,
            "padding": padding,
            "overview_data": overview_data,
            "days_in_month": days_in_month,
            "view": view,
            "sun_first_labels": SUN_FIRST_LABELS,
            "weekday_labels": WEEKDAY_LABELS,
            "date_type_labels": DATE_TYPE_LABELS,
            "date_type_colors": DATE_TYPE_COLORS,
            "overtime_multipliers": OVERTIME_MULTIPLIERS,
            "holidays": holiday_objects,
            "holiday_map": holiday_map,
        },
    )


@router.post("/settings/{caregiver_id}")
async def update_attendance_settings(
    caregiver_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    target = db.query(User).filter(User.id == caregiver_id).first()
    if not target:
        return RedirectResponse(url="/attendance", status_code=302)
    form = await request.form()
    target.hourly_wage = int(form.get("hourly_wage")) if form.get("hourly_wage") else None
    wkdays = form.getlist("work_weekdays")
    target.work_weekdays = sorted({int(v) for v in wkdays if v.strip()}) if wkdays else None
    target.force_overtime_weekend = form.get("force_overtime_weekend") == "on"
    db.commit()
    return RedirectResponse(
        url=f"/attendance?caregiver_id={caregiver_id}", status_code=302
    )


@router.post("/settings/batch")
async def batch_update_settings(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    form = await request.form()
    today = date.today()
    caregivers = (
        db.query(User)
        .filter(
            User.role == UserRole.caregiver,
            User.is_active.is_(True),
            (User.termination_date.is_(None)) | (User.termination_date > today),
        )
        .all()
    )
    for cg in caregivers:
        off_key = f"off_{cg.id}"
        rest_key = f"rest_{cg.id}"
        wage_key = f"wage_{cg.id}"
        wk_key = f"wk_{cg.id}"
        if off_key in form:
            val = form.get(off_key, "")
            cg.regular_off_weekday = int(val) if val else None
        if rest_key in form:
            val = form.get(rest_key, "")
            cg.rest_weekday = int(val) if val else None
        if wage_key in form:
            val = form.get(wage_key, "")
            cg.hourly_wage = int(val) if val else None
        wkdays = form.getlist(wk_key)
        cg.work_weekdays = sorted({int(v) for v in wkdays if v.strip()}) if wkdays else None
        fo_key = f"fo_{cg.id}"
        cg.force_overtime_weekend = fo_key in form
    db.commit()
    return RedirectResponse(url="/attendance?view=overview", status_code=302)


@router.post("/holidays/add")
def add_holiday(
    holiday_date: str = Form(...),
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    try:
        d = date.fromisoformat(holiday_date)
    except ValueError:
        return RedirectResponse(url="/attendance?error=日期格式錯誤", status_code=302)
    existing = db.query(NationalHoliday).filter(NationalHoliday.holiday_date == d).first()
    if existing:
        existing.name = name.strip()
    else:
        db.add(NationalHoliday(holiday_date=d, name=name.strip(), year=d.year))
    db.commit()
    return RedirectResponse(url="/attendance", status_code=302)


@router.post("/holidays/delete/{holiday_id}")
def delete_holiday(
    holiday_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    holiday = db.query(NationalHoliday).filter(NationalHoliday.id == holiday_id).first()
    if holiday:
        db.delete(holiday)
        db.commit()
    return RedirectResponse(url="/attendance", status_code=302)


@router.post("/holidays/seed")
def seed_holidays(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    seed_national_holidays(db)
    return RedirectResponse(url="/attendance", status_code=302)
