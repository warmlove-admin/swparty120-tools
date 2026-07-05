import math
from collections import defaultdict
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.import_salary_record import ImportSalaryRecord
from app.models.leave import LeaveType, LeaveRequest, LeaveRequestStatus
from app.models.user import User, UserRole

router = APIRouter(prefix="/annual-leave")
templates = Jinja2Templates(directory="app/templates")


def _annual_leave_entitlement_days(years: float) -> int:
    if years >= 10:
        return min(15 + int(years - 10), 30)
    if years >= 5:
        return 15
    if years >= 3:
        return 14
    if years >= 2:
        return 10
    if years >= 1:
        return 7
    if years >= 0.5:
        return 3
    return 0


def _safe_replace_year(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


def _get_leave_year_start(arrival_date: date, settlement_end: date) -> date:
    """找出 settlement_end 所屬特休年度的起始日（前一個周年日）"""
    for n in range(settlement_end.year - arrival_date.year, -1, -1):
        ann = _safe_replace_year(arrival_date, arrival_date.year + n)
        if ann <= settlement_end:
            return ann
    return arrival_date


def _get_used_annual_leave_days(caregiver_id: str, since: date, until: date, db: Session) -> float:
    """計算區間內已核准的特休天數"""
    annual_type = db.query(LeaveType).filter(LeaveType.code == "annual").first()
    if not annual_type:
        return 0.0
    result = db.query(db.func.coalesce(db.func.sum(LeaveRequest.days), 0)).filter(
        LeaveRequest.caregiver_id == caregiver_id,
        LeaveRequest.leave_type_id == annual_type.id,
        LeaveRequest.status == LeaveRequestStatus.approved,
        LeaveRequest.start_date >= since,
        LeaveRequest.start_date <= until,
    ).scalar()
    return float(result or 0.0)


def _find_last_settlement_end(hire: date, today: date) -> date | None:
    """
    找出最近一次結算迄日（周年日 - 1 天）。
    結算迄日必須 <= today。
    例：到職 2021-04-28，今天 2026-07-04
        周年日 2026-04-28 → 結算迄日 2026-04-27（已過）→ 採用
    例：到職 2023-05-30，今天 2026-07-04
        周年日 2026-05-30 → 結算迄日 2026-05-29（已過）→ 採用
    """
    for year in range(today.year, hire.year - 1, -1):
        ann = _safe_replace_year(hire, year)
        settlement_end = ann - relativedelta(days=1)
        if settlement_end <= today:
            return settlement_end
    return None


def caregiver_annual_leave_data(
    caregiver_id: str, db: Session
) -> dict:
    cg = db.query(User).filter(User.id == caregiver_id).first()
    if not cg:
        return {"error": "找不到居服員"}

    today = date.today()
    hire = cg.hire_date
    wage = cg.hourly_wage or 230
    wage = int(wage)

    if not hire:
        return {"error": "無到職日"}

    if hire > today:
        return {"error": "到職日尚未到達"}

    # 結算迄日 = 最近一次周年日 - 1 天
    settlement_end = _find_last_settlement_end(hire, today)
    if settlement_end is None:
        return {"error": "尚未滿第一個周年（無結算資料）"}

    # 周年日 = 結算迄日 + 1 天
    anniversary = settlement_end + relativedelta(days=1)
    years_int = anniversary.year - hire.year
    years_float = (settlement_end - hire).days / 365.25

    leave_days = _annual_leave_entitlement_days(years_int - 0.5)

    # 計算區間（過去12個月，不含結算迄日當月）
    settlement_month_start = date(settlement_end.year, settlement_end.month, 1)
    data_start = settlement_month_start - relativedelta(months=12)
    data_end = settlement_month_start - relativedelta(days=1)

    records = (
        db.query(ImportSalaryRecord)
        .filter(
            ImportSalaryRecord.caregiver_id == caregiver_id,
            ImportSalaryRecord.service_date >= data_start,
            ImportSalaryRecord.service_date <= data_end,
        )
        .all()
    )

    total_minutes = sum(
        (r.transfer_minutes or 0) + (r.weekday_0_8 or 0)
        for r in records
    )
    total_hours = total_minutes / 60.0
    ratio = min(total_hours / 2088.0, 1.0)

    entitled_hours = leave_days * 8 * ratio

    # 扣除已核准特休
    leave_year_start = _get_leave_year_start(hire, settlement_end)
    used_days = _get_used_annual_leave_days(caregiver_id, leave_year_start, settlement_end, db)
    used_hours = used_days * 8 * ratio
    remaining_days = max(0, leave_days - used_days)
    unused_hours = round(remaining_days * 8 * ratio, 2)

    payout = round(unused_hours * wage)
    calc_period = f"{data_start.strftime('%Y/%m/%d')}～{data_end.strftime('%Y/%m/%d')}"

    return {
        "caregiver_name": cg.display_name,
        "hire_date": hire,
        "ref_date": settlement_end,
        "anniversary": anniversary,
        "anniversary_age": years_int,
        "years": years_float,
        "calc_period": calc_period,
        "leave_days": leave_days,
        "past_12m_start": data_start,
        "past_12m_end": data_end,
        "total_hours_12m": round(total_hours, 2),
        "ratio": round(ratio, 4),
        "ratio_percent": f"{ratio*100:.1f}%",
        "entitled_hours": round(entitled_hours, 2),
        "used_days": used_days,
        "remaining_days": remaining_days,
        "unused_hours": unused_hours,
        "wage": wage,
        "payout": payout,
    }


def caregiver_resigned_leave_data(caregiver_id: str, db: Session) -> dict:
    """離職人員特休結算：以離職日最近發薪日為結算迄日"""
    cg = db.query(User).filter(User.id == caregiver_id).first()
    if not cg:
        return {"error": "找不到居服員"}

    termination = cg.termination_date
    if not termination:
        return {"error": "無離職日期"}

    hire = cg.hire_date
    wage = cg.hourly_wage or 230
    wage = int(wage)

    if not hire:
        return {"error": "無到職日"}

    # 結算迄日 = 離職生效日的前一天（最後工作日）
    settlement_end = termination - relativedelta(days=1)

    # 找出最近周年日
    anniversary = None
    for year in range(settlement_end.year, hire.year - 1, -1):
        ann = _safe_replace_year(hire, year)
        if ann <= settlement_end:
            anniversary = ann
            break

    if anniversary is None:
        return {"error": "尚未滿第一個周年"}

    years_int = (anniversary.year - hire.year) + 1
    years_float = (settlement_end - hire).days / 365.25
    leave_days = _annual_leave_entitlement_days(years_int - 0.5)

    # 計算區間（過去12個月，不含結算迄日當月）
    settlement_month_start = date(settlement_end.year, settlement_end.month, 1)
    data_start = settlement_month_start - relativedelta(months=12)
    data_end = settlement_month_start - relativedelta(days=1)

    records = (
        db.query(ImportSalaryRecord)
        .filter(
            ImportSalaryRecord.caregiver_id == caregiver_id,
            ImportSalaryRecord.service_date >= data_start,
            ImportSalaryRecord.service_date <= data_end,
        )
        .all()
    )

    total_minutes = sum(
        (r.transfer_minutes or 0) + (r.weekday_0_8 or 0)
        for r in records
    )
    total_hours = total_minutes / 60.0
    ratio = min(total_hours / 2088.0, 1.0)

    entitled_hours = leave_days * 8 * ratio

    # 扣除已核准特休
    leave_year_start = _get_leave_year_start(hire, settlement_end)
    used_days = _get_used_annual_leave_days(caregiver_id, leave_year_start, settlement_end, db)
    used_hours = used_days * 8 * ratio
    remaining_days = max(0, leave_days - used_days)
    unused_hours = round(remaining_days * 8 * ratio, 2)

    payout = round(unused_hours * wage)
    calc_period = f"{data_start.strftime('%Y/%m/%d')}～{data_end.strftime('%Y/%m/%d')}"

    return {
        "caregiver_name": cg.display_name,
        "hire_date": hire,
        "termination_date": termination,
        "ref_date": settlement_end,
        "anniversary": anniversary,
        "anniversary_age": years_int,
        "years": round(years_float, 2),
        "calc_period": calc_period,
        "leave_days": leave_days,
        "past_12m_start": data_start,
        "past_12m_end": data_end,
        "total_hours_12m": round(total_hours, 2),
        "ratio": round(ratio, 4),
        "ratio_percent": f"{ratio*100:.1f}%",
        "entitled_hours": round(entitled_hours, 2),
        "used_days": used_days,
        "remaining_days": remaining_days,
        "unused_hours": unused_hours,
        "wage": wage,
        "payout": payout,
        "note": "離職結清",
    }


@router.get("", response_class=HTMLResponse)
def annual_leave_page(
    request: Request,
    caregiver_id: str = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director)),
):
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

    calc_result = None
    if caregiver_id:
        calc_result = caregiver_annual_leave_data(caregiver_id, db)

    return templates.TemplateResponse(
        request,
        "annual_leave.html",
        {
            "user": user,
            "caregivers": caregivers,
            "selected_caregiver_id": caregiver_id,
            "calc": calc_result,
        },
    )