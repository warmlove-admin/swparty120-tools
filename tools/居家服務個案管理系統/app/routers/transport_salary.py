import calendar
import io
import math
from datetime import date, datetime

import pandas as pd
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.caregiver_transfer import CaregiverTransfer
from app.models.monthly_salary import MonthlySalary
from app.models.user import User, UserRole
from app.services.salary_engine import (
    calculate_all_monthly_salaries,
    calculate_all_transfers,
    get_daily_bracket_breakdown,
)

router = APIRouter(prefix="/transport-salary")
templates = Jinja2Templates(directory="app/templates")


def _parse_month(value: str | None) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1) if value else date.today().replace(day=1)
    except ValueError:
        return date.today().replace(day=1)


def _month_display(d: date) -> str:
    return f"{d.year}年{d.month}月"


def _month_options() -> list[date]:
    today = date.today()
    options = []
    for i in range(6):
        m = today.month - i
        y = today.year
        while m < 1:
            m += 12
            y -= 1
        options.append(date(y, m, 1))
    return options


def _get_caregivers_with_data(year: int, month: int, db: Session) -> list[User]:
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    cg_ids = (
        db.query(CaregiverServiceRecord.caregiver_id)
        .filter(
            CaregiverServiceRecord.service_date >= month_start,
            CaregiverServiceRecord.service_date <= month_end,
        )
        .distinct()
        .all()
    )
    if not cg_ids:
        return []
    ids = [cid for (cid,) in cg_ids]
    return (
        db.query(User)
        .filter(User.id.in_(ids))
        .order_by(User.display_name)
        .all()
    )


def _get_transfer_stats(year: int, month: int, db: Session) -> dict:
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    total = db.query(CaregiverTransfer).filter(
        CaregiverTransfer.service_date >= month_start,
        CaregiverTransfer.service_date <= month_end,
    ).count()
    if total == 0:
        return {"total": 0, "ok": 0, "failed": 0, "pending": 0}
    ok = db.query(CaregiverTransfer).filter(
        CaregiverTransfer.service_date >= month_start,
        CaregiverTransfer.service_date <= month_end,
        CaregiverTransfer.status.in_(["OK", "SAME_ADDR", "CACHED"]),
    ).count()
    failed = db.query(CaregiverTransfer).filter(
        CaregiverTransfer.service_date >= month_start,
        CaregiverTransfer.service_date <= month_end,
        CaregiverTransfer.status.like("FAILED%"),
    ).count()
    return {"total": total, "ok": ok, "failed": failed, "pending": total - ok - failed}


# ── 主頁 ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def transport_salary_index(
    request: Request,
    month: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director, UserRole.accountant)),
):
    current_month = _parse_month(month)
    caregivers = _get_caregivers_with_data(current_month.year, current_month.month, db)
    transfer_stats = _get_transfer_stats(current_month.year, current_month.month, db)

    salary_records = (
        db.query(MonthlySalary)
        .filter(
            MonthlySalary.year == current_month.year,
            MonthlySalary.month == current_month.month,
        )
        .all()
    )
    salary_map = {s.caregiver_id: s for s in salary_records}

    results = []
    for cg in caregivers:
        s = salary_map.get(cg.id)
        results.append({
            "caregiver": cg,
            "salary": s,
        })

    return templates.TemplateResponse(
        request, "transport_salary.html", {
            "user": user,
            "month_options": _month_options(),
            "current_month": current_month,
            "month_display": _month_display(current_month),
            "results": results,
            "transfer_stats": transfer_stats,
            "error": error,
        }
    )


# ── 執行轉場距離計算 ──────────────────────────────────────────────────────

@router.post("/calculate-transfers")
def run_calculate_transfers(
    month: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    current_month = _parse_month(month)
    year, mon = current_month.year, current_month.month
    try:
        stats = calculate_all_transfers(year, mon, db)
        msg = (
            f"轉場計算完成：{stats['caregivers']} 位居服員，"
            f"成功 {stats['ok']} 筆、快取 {stats['cached']} 筆、"
            f"跳過 {stats['skipped']} 筆、失敗 {stats['failed']} 筆"
        )
        return RedirectResponse(
            url=f"/transport-salary?month={month}&success={msg}", status_code=302
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/transport-salary?month={month}&error=計算失敗：{str(e)[:200]}", status_code=302
        )


# ── 執行薪資試算 ──────────────────────────────────────────────────────────

@router.post("/calculate-salaries")
def run_calculate_salaries(
    month: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    current_month = _parse_month(month)
    try:
        results = calculate_all_monthly_salaries(
            current_month.year, current_month.month, db, calculated_by=user.id
        )
        return RedirectResponse(
            url=f"/transport-salary?month={month}&success=薪資試算完成：共 {len(results)} 位居服員",
            status_code=302
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/transport-salary?month={month}&error=試算失敗：{str(e)[:200]}", status_code=302
        )


# ── 每日明細 ──────────────────────────────────────────────────────────────

@router.get("/detail/{caregiver_id}", response_class=HTMLResponse)
def transport_salary_detail(
    request: Request,
    caregiver_id: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director, UserRole.accountant)),
):
    current_month = _parse_month(month)
    caregiver = db.query(User).filter(User.id == caregiver_id).first()
    if not caregiver:
        return RedirectResponse(url="/transport-salary", status_code=302)

    _, days_in_month = calendar.monthrange(current_month.year, current_month.month)
    daily_details = []
    for day in range(1, days_in_month + 1):
        d = date(current_month.year, current_month.month, day)
        detail = get_daily_bracket_breakdown(caregiver_id, d, db)
        if detail:
            daily_details.append(detail)

    salary = db.query(MonthlySalary).filter(
        MonthlySalary.caregiver_id == caregiver_id,
        MonthlySalary.year == current_month.year,
        MonthlySalary.month == current_month.month,
    ).first()

    return templates.TemplateResponse(
        request, "transport_salary_detail.html", {
            "user": user,
            "caregiver": caregiver,
            "current_month": current_month,
            "month_display": _month_display(current_month),
            "daily_details": daily_details,
            "salary": salary,
        }
    )


# ── 匯出 Excel ────────────────────────────────────────────────────────────

@router.get("/export")
def export_transport_salary(
    month: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director, UserRole.accountant)),
):
    current_month = _parse_month(month)
    caregivers = _get_caregivers_with_data(current_month.year, current_month.month, db)
    salary_records = (
        db.query(MonthlySalary)
        .filter(
            MonthlySalary.year == current_month.year,
            MonthlySalary.month == current_month.month,
        )
        .all()
    )
    salary_map = {s.caregiver_id: s for s in salary_records}

    rows = []
    for cg in caregivers:
        s = salary_map.get(cg.id)
        rows.append({
            "員工姓名": cg.display_name,
            "服務分鐘數": s.total_service_minutes if s else 0,
            "加權分鐘(不含交通)": s.weighted_minutes_no_transport if s else 0,
            "加權分鐘(含交通)": s.weighted_minutes_with_transport if s else 0,
            "轉場公里": s.total_transfer_km if s else 0,
            "轉場分鐘": s.total_transfer_minutes if s else 0,
            "不含交通薪資": s.salary_no_transport if s else 0,
            "含交通薪資": s.salary_with_transport if s else 0,
            "交通津貼": s.transport_allowance if s else 0,
            "久任獎金": s.long_term_bonus if s else 0,
            "總薪資": (s.salary_with_transport or 0) + (s.long_term_bonus or 0) if s else 0,
            "時薪": cg.hourly_wage or 230,
        })

    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="每月總計", index=False)
    output.seek(0)

    filename = f"交通津貼薪資_{current_month.year}-{current_month.month:02d}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
