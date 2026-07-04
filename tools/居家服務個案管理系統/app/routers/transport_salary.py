import calendar
import io
import math
import uuid
from datetime import date, datetime

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.caregiver_transfer import CaregiverTransfer
from app.models.case import Case
from app.models.import_salary_record import ImportSalaryRecord
from app.models.monthly_salary import MonthlySalary
from app.models.user import User, UserRole
from app.services.attendance_engine import (
    BRACKET_CAPS_MINUTES,
    DATE_TYPE_LABELS,
    classify_date_from_records,
)
from app.services.salary_engine import (
    calculate_all_import_transfers,
    calculate_all_monthly_salaries,
    calculate_all_monthly_salaries_from_import,
    calculate_all_transfers,
    get_daily_bracket_breakdown,
)

BRACKET_COL_INDEX = {
    11: ("weekday", "0-8"), 13: ("weekday", "9-10"), 15: ("weekday", "11-12"),
    17: ("national_holiday", "0-8"), 19: ("national_holiday", "9-10"), 21: ("national_holiday", "11-12"),
    23: ("regular_off", "0-2"), 25: ("regular_off", "3-8"), 27: ("regular_off", "9-10"), 29: ("regular_off", "11-12"),
    31: ("rest_day", "0-2"), 33: ("rest_day", "3-8"), 35: ("rest_day", "9-10"), 37: ("rest_day", "11-12"),
}

BRACKET_DB_COL = {
    ("weekday", "0-8"): "weekday_0_8",
    ("weekday", "9-10"): "weekday_9_10",
    ("weekday", "11-12"): "weekday_11_12",
    ("national_holiday", "0-8"): "national_holiday_0_8",
    ("national_holiday", "9-10"): "national_holiday_9_10",
    ("national_holiday", "11-12"): "national_holiday_11_12",
    ("regular_off", "0-2"): "regular_off_0_2",
    ("regular_off", "3-8"): "regular_off_3_8",
    ("regular_off", "9-10"): "regular_off_9_10",
    ("regular_off", "11-12"): "regular_off_11_12",
    ("rest_day", "0-2"): "rest_day_0_2",
    ("rest_day", "3-8"): "rest_day_3_8",
    ("rest_day", "9-10"): "rest_day_9_10",
    ("rest_day", "11-12"): "rest_day_11_12",
}

router = APIRouter(prefix="/transport-salary")
templates = Jinja2Templates(directory="app/templates")


def _parse_month(value: str | None) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1) if value else date.today().replace(day=1)
    except ValueError:
        return date.today().replace(day=1)


def _month_display(d: date) -> str:
    return f"{d.year}年{d.month}月"


def _month_options(db: Session) -> list[date]:
    from sqlalchemy import func
    rows = (
        db.query(
            func.extract("year", CaregiverServiceRecord.service_date).label("y"),
            func.extract("month", CaregiverServiceRecord.service_date).label("m"),
        )
        .distinct()
        .order_by(func.extract("year", CaregiverServiceRecord.service_date).desc(),
                  func.extract("month", CaregiverServiceRecord.service_date).desc())
        .all()
    )
    return [date(int(r.y), int(r.m), 1) for r in rows]


def _has_import_data(year: int, month: int, db: Session) -> bool:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    return db.query(ImportSalaryRecord).filter(
        ImportSalaryRecord.service_date >= date(year, month, 1),
        ImportSalaryRecord.service_date <= date(year, month, days_in_month),
    ).first() is not None


def _get_caregivers_with_data(year: int, month: int, db: Session) -> list[User]:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    cg_from_csv = set(
        cid for (cid,) in db.query(CaregiverServiceRecord.caregiver_id).filter(
            CaregiverServiceRecord.service_date >= month_start,
            CaregiverServiceRecord.service_date <= month_end,
        ).distinct().all()
    )
    cg_from_import = set(
        cid for (cid,) in db.query(ImportSalaryRecord.caregiver_id).filter(
            ImportSalaryRecord.service_date >= month_start,
            ImportSalaryRecord.service_date <= month_end,
        ).distinct().all()
    )

    ids = list(cg_from_csv | cg_from_import)
    if not ids:
        return []
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


def _get_daily_visit_details(caregiver_id: str, svc_date: date, db: Session) -> dict | None:
    records = (
        db.query(CaregiverServiceRecord)
        .filter(
            CaregiverServiceRecord.caregiver_id == caregiver_id,
            CaregiverServiceRecord.service_date == svc_date,
        )
        .order_by(CaregiverServiceRecord.start_time)
        .all()
    )
    if not records:
        return None

    visits = []
    for r in records:
        visits.append({
            "visit_id": r.id,
            "case_name": r.case_name_raw,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "minutes": r.minutes,
        })

    transfers = []
    for i in range(len(records) - 1):
        curr = records[i]
        next_r = records[i + 1]
        t = db.query(CaregiverTransfer).filter(
            CaregiverTransfer.from_visit_id == curr.id,
            CaregiverTransfer.to_visit_id == next_r.id,
        ).first()
        if t:
            transfers.append({
                "from_case": t.from_case_name,
                "to_case": t.to_case_name,
                "km": t.transfer_km,
                "minutes": t.transfer_minutes,
                "status": t.status,
            })

    return {"visits": visits, "transfers": transfers}


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

    has_import = _has_import_data(current_month.year, current_month.month, db)

    return templates.TemplateResponse(
        request, "transport_salary.html", {
            "user": user,
            "month_options": _month_options(db),
            "current_month": current_month,
            "month_display": _month_display(current_month),
            "results": results,
            "transfer_stats": transfer_stats,
            "error": error,
            "has_import_data": has_import,
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


# ── 執行薪資試算（使用匯入資料） ──────────────────────────────────────────

@router.post("/calculate-salaries-from-import")
def run_calculate_salaries_from_import(
    month: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    current_month = _parse_month(month)
    try:
        results = calculate_all_monthly_salaries_from_import(
            current_month.year, current_month.month, db, calculated_by=user.id
        )
        return RedirectResponse(
            url=f"/transport-salary?month={month}&success=薪資試算（實際服務）完成：共 {len(results)} 位居服員",
            status_code=302
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/transport-salary?month={month}&error=試算失敗：{str(e)[:200]}", status_code=302
        )


# ── 轉場計算（使用匯入資料） ──────────────────────────────────────────────

@router.post("/calculate-import-transfers")
def run_calculate_import_transfers(
    month: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    current_month = _parse_month(month)
    year, mon = current_month.year, current_month.month
    try:
        stats = calculate_all_import_transfers(year, mon, db)
        msg = (
            f"實際服務轉場計算完成：{stats['caregivers']} 位居服員，"
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


# ── 上傳時薪明細表 ─────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_salary_xlsx(
    request: Request,
    month: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    current_month = _parse_month(month)
    batch_id = str(uuid.uuid4())
    filename = file.filename or "unknown.xlsx"

    if not filename.endswith((".xlsx", ".xls")):
        return RedirectResponse(
            url=f"/transport-salary?month={month}&error=請上傳 .xlsx 檔案",
            status_code=302
        )

    try:
        content = await file.read()
        df = pd.read_excel(io.BytesIO(content), sheet_name=0, header=0)
    except Exception as e:
        return RedirectResponse(
            url=f"/transport-salary?month={month}&error=無法讀取 Excel：{str(e)[:200]}",
            status_code=302
        )

    # 清理欄位名稱
    df.columns = [str(c).strip() for c in df.columns]

    # 檢查必要欄位
    needed = {"員工姓名", "服務日期"}
    # 個案欄位可能是「個案姓名」或「個案名稱」
    case_col = None
    for c in df.columns:
        s = str(c).strip()
        if s in ("個案姓名", "個案名稱"):
            case_col = s
            break
    if not case_col or not needed.issubset(set(df.columns)):
        return RedirectResponse(
            url=f"/transport-salary?month={month}&error=Excel 缺少必要欄位：員工姓名、服務日期、個案姓名/名稱",
            status_code=302
        )

    # 取得所有居服員與個案的對照表
    all_caregivers = {
        u.display_name: u.id
        for u in db.query(User).filter(User.role == UserRole.caregiver).all()
    }
    all_cases = {
        c.name: c.id
        for c in db.query(Case).all()
    }

    imported = 0
    skipped_no_cg = 0
    skipped_no_case = 0
    skipped_no_date = 0

    # 先刪除該月既有匯入資料
    if current_month.month == 12:
        db.query(ImportSalaryRecord).filter(
            ImportSalaryRecord.service_date >= current_month,
            ImportSalaryRecord.service_date <= date(current_month.year, 12, 31),
            ImportSalaryRecord.import_batch_id != batch_id,
        ).delete()
    else:
        next_month = date(current_month.year, current_month.month + 1, 1)
        db.query(ImportSalaryRecord).filter(
            ImportSalaryRecord.service_date >= current_month,
            ImportSalaryRecord.service_date < next_month,
            ImportSalaryRecord.import_batch_id != batch_id,
        ).delete()

    numeric_cols = {}
    for col_idx, (dt, bracket) in BRACKET_COL_INDEX.items():
        if col_idx < len(df.columns):
            db_col = BRACKET_DB_COL.get((dt, bracket))
            if db_col:
                col_name = str(df.columns[col_idx])
                numeric_cols[col_name] = db_col
                df[col_name] = pd.to_numeric(df[col_name], errors="coerce").fillna(0)

    # Check for 服務地址 column
    addr_col = None
    for c in df.columns:
        if "服務地址" in str(c) or "服務住址" in str(c) or "地址" in str(c):
            addr_col = str(c)
            break

    hourly_wage_col = None
    for c in df.columns:
        if str(c).strip() == "時薪":
            hourly_wage_col = str(c)
            break

    transfer_min_col = None
    for c in df.columns:
        s = str(c).strip()
        if s == "轉場分鐘" or "轉場分鐘" in s:
            transfer_min_col = str(c)
            break

    _visit_counter = {}

    for _, row in df.iterrows():
        cg_name = str(row["員工姓名"]).strip()
        case_name = str(row[case_col]).strip()

        # Parse date
        svc_date = row["服務日期"]
        if pd.isna(svc_date):
            skipped_no_date += 1
            continue
        try:
            if isinstance(svc_date, datetime):
                svc_date = svc_date.date()
            elif isinstance(svc_date, date):
                pass
            else:
                svc_date = pd.to_datetime(svc_date).date()
        except Exception:
            skipped_no_date += 1
            continue

        # Skip if not matching current month
        if svc_date.year != current_month.year or svc_date.month != current_month.month:
            continue

        caregiver_id = all_caregivers.get(cg_name)
        case_id = all_cases.get(case_name)

        if not caregiver_id:
            skipped_no_cg += 1
            continue
        if not case_id:
            skipped_no_case += 1
            continue

        key = (cg_name, svc_date)
        _visit_counter[key] = _visit_counter.get(key, 0) + 1
        visit_order = _visit_counter[key]

        transfer_mins = 0.0
        if transfer_min_col:
            try:
                transfer_mins = float(row.get(transfer_min_col, 0) or 0)
            except (ValueError, TypeError):
                pass

        # Compute bracket values
        bracket_vals = {}
        total_min = 0
        for col_name, db_col in numeric_cols.items():
            val = int(row.get(col_name, 0) or 0)
            bracket_vals[db_col] = val
            total_min += val

        # Fill minutes
        fill_min = 0
        fill_col = None
        for c in df.columns:
            if "補滿服務分鐘" in str(c) or "補滿" in str(c):
                fill_col = str(c)
                break
        if fill_col:
            fill_min = int(row.get(fill_col, 0) or 0)
            if fill_min > 0:
                total_min += fill_min

        hourly_wage = None
        if hourly_wage_col:
            try:
                hourly_wage = int(float(row.get(hourly_wage_col, 0) or 0))
            except (ValueError, TypeError):
                pass

        address = ""
        if addr_col:
            address = str(row.get(addr_col, "") or "")

        rec = ImportSalaryRecord(
            caregiver_id=caregiver_id,
            caregiver_name_raw=cg_name,
            case_id=case_id,
            case_name_raw=case_name,
            service_date=svc_date,
            service_address=address,
            hourly_wage=hourly_wage or None,
            fill_minutes=fill_min,
            total_minutes=total_min,
            visit_order=visit_order,
            transfer_minutes=transfer_mins,
            source_filename=filename,
            import_batch_id=batch_id,
            upload_user_id=user.id,
            **bracket_vals,
        )
        db.add(rec)
        imported += 1

    db.commit()

    parts = [f"匯入完成：{imported} 筆"]
    if skipped_no_cg:
        parts.append(f"跳過（無對應居服員）{skipped_no_cg}")
    if skipped_no_case:
        parts.append(f"跳過（無對應個案）{skipped_no_case}")
    if skipped_no_date:
        parts.append(f"跳過（無日期）{skipped_no_date}")
    if imported > 0:
        # Show a few unmatched case names if any
        unmatched_cases = set()
        for _, row in df.iterrows():
            svc_date = row["服務日期"]
            if pd.isna(svc_date):
                continue
            cname = str(row[case_col]).strip()
            if cname not in all_cases:
                unmatched_cases.add(cname)
        if unmatched_cases:
            sample = "; ".join(list(unmatched_cases)[:5])
            parts.append(f"無對應個案：{sample}")

    return RedirectResponse(
        url=f"/transport-salary?month={month}&success={'，'.join(parts)}",
        status_code=302
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

    daily_visits: list[dict] = []
    for day in range(1, days_in_month + 1):
        d = date(current_month.year, current_month.month, day)
        dd = _get_daily_visit_details(caregiver_id, d, db)
        if dd:
            daily_visits.append({"date": d, "visits": dd["visits"], "transfers": dd["transfers"]})

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
            "daily_visits": daily_visits,
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
    from urllib.parse import quote
    ascii_name = f"salary_{current_month.year}-{current_month.month:02d}.xlsx"
    filename_header = f"attachment; filename={ascii_name}; filename*=UTF-8''{quote(f'交通津貼薪資_{current_month.year}-{current_month.month:02d}.xlsx')}"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": filename_header},
    )
