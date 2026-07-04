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
    """replace year，處理 2/29 在非閏年的情況"""
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


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
    # 例：結算迄日 2025-09-13 → 計算區間 2024/9/1 ~ 2025/8/31
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

    # 工時 = 轉場分鐘 + 平日0-8（與 special_leave_calc.py 一致）
    total_minutes = sum(
        (r.transfer_minutes or 0) + (r.weekday_0_8 or 0)
        for r in records
    )
    total_hours = total_minutes / 60.0
    ratio = min(total_hours / 2088.0, 1.0)

    entitled_hours = leave_days * 8 * ratio

    # 居服員一律全數未休
    unused_hours = round(entitled_hours, 2)
    payout = round(unused_hours * wage)

    # 計算區間字串
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
        "unused_hours": unused_hours,
        "wage": wage,
        "payout": payout,
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