import calendar
import io
import math
import os
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.caregiver_transfer import CaregiverTransfer
from app.models.case import Case
from app.models.monthly_salary import MonthlySalary
from app.models.salary_item import SalaryItem
from app.models.salary_payment import SalaryPayment
from app.models.aa_code import AaCodeRecord, Aa06CaseCondition
from app.models.user import User, UserRole
from app.services.attendance_engine import (
    BRACKET_CAPS_MINUTES,
    DATE_TYPE_LABELS,
    classify_date_from_records,
)
from app.services.aa_code_import import (
    import_aa_file,
    save_allocations,
    parse_aa_excel,
    EXCLUDED_AA_CODES,
    AA06_CONDITION_BA,
)
from app.services.salary_engine import (
    calc_travel_allowance,
    calculate_all_monthly_salaries,
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

# In-memory background job tracking
_jobs: dict[str, dict] = {}


def _parse_month(value: str | None, db: Session = None) -> date:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m").date().replace(day=1)
        except ValueError:
            pass
    # 無指定月份時，選資料庫裡最新有資料的月份
    if db:
        options = _month_options(db)
        if options:
            return options[0]
    return date.today().replace(day=1)


def _month_display(d: date) -> str:
    return f"{d.year}年{d.month}月"


def _month_options(db: Session) -> list[date]:
    from sqlalchemy import func
    months = set()
    
    # 排班表月份
    rows = (
        db.query(
            func.extract("year", CaregiverServiceRecord.service_date).label("y"),
            func.extract("month", CaregiverServiceRecord.service_date).label("m"),
        )
        .distinct()
        .all()
    )
    for r in rows:
        months.add((int(r.y), int(r.m)))
    
    # 加入當月與次月（讓使用者可以提前開新月份）
    today = date.today()
    months.add((today.year, today.month))
    if today.month == 12:
        months.add((today.year + 1, 1))
    else:
        months.add((today.year, today.month + 1))
    
    return [date(y, m, 1) for y, m in sorted(months, reverse=True)]


def _has_import_data(year: int, month: int, db: Session) -> bool:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    return db.query(CaregiverServiceRecord).filter(
        CaregiverServiceRecord.service_date >= date(year, month, 1),
        CaregiverServiceRecord.service_date <= date(year, month, days_in_month),
    ).first() is not None


def _get_pending_aa06_cases(db: Session, year: int, month: int) -> list[dict]:
    """回傳當月有 AA06 但尚未設定條件的個案清單"""
    aa06_in_month = db.query(AaCodeRecord).filter(
        AaCodeRecord.year == year,
        AaCodeRecord.month == month,
        AaCodeRecord.aa_code == "AA06",
        AaCodeRecord.caregiver_share == 0,
    ).all()
    seen = {}
    for rec in aa06_in_month:
        if rec.case_id in seen:
            continue
        cond = db.query(Aa06CaseCondition).filter(Aa06CaseCondition.case_id == rec.case_id).first()
        if not cond:
            cg_names = set()
            for r2 in aa06_in_month:
                if r2.case_id == rec.case_id and r2.caregiver:
                    cg_names.add(r2.caregiver.display_name)
            seen[rec.case_id] = {
                "case_name": rec.case.name if rec.case else "（已刪除）",
                "case_id": rec.case_id,
                "caregivers": sorted(cg_names),
            }
    result = list(seen.values())
    result.sort(key=lambda x: x["case_name"])
    return result


def _get_caregivers_with_data(year: int, month: int, db: Session) -> list[User]:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    cg_from_records = set(
        cid for (cid,) in db.query(CaregiverServiceRecord.caregiver_id).filter(
            CaregiverServiceRecord.service_date >= month_start,
            CaregiverServiceRecord.service_date <= month_end,
        ).distinct().all()
    )
    cg_from_aa = set(
        cid for (cid,) in db.query(AaCodeRecord.caregiver_id).filter(
            AaCodeRecord.year == year,
            AaCodeRecord.month == month,
        ).distinct().all()
    )
    cg_from_ms = set(
        cid for (cid,) in db.query(MonthlySalary.caregiver_id).filter(
            MonthlySalary.year == year,
            MonthlySalary.month == month,
        ).distinct().all()
    )

    ids = list(cg_from_records | cg_from_aa | cg_from_ms)
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


# ── 薪資項目對照表（SalaryItem → MonthlySalary 欄位） ─────────────────────

MS_MAP = {
    "本薪（含交通）": "salary_with_transport",
    "交通津貼": "transport_allowance",
    "久任獎金": "long_term_bonus",
    "AA碼獎金": "aa_bonus",
}


# ── 主頁 ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def transport_salary_index(
    request: Request,
    month: str | None = None,
    tab: str = "salary",
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director, UserRole.accountant)),
):
    current_month = _parse_month(month, db)
    # AA 明細預設顯示同月份（非薪資分頁時適用）
    aa_ref_month_display = _month_display(current_month)

    # ── 久任獎金分頁 ──────────────────────────────────────────────────────
    if tab == "long_term_bonus":
        return _handle_long_term_bonus(request, current_month, db, user)

    # ── AA 碼獎金分頁 ────────────────────────────────────────────────────
    if tab == "aa_bonus":
        aa_records = db.query(AaCodeRecord).filter(
            AaCodeRecord.year == current_month.year,
            AaCodeRecord.month == current_month.month,
        ).all()
        # 過濾掉 caregiver_share=0（AA06 未設定條件的暫存記錄）
        aa_records = [r for r in aa_records if r.caregiver_share > 0]
        # 依居服員彙總
        aa_by_cg = defaultdict(list)
        for r in aa_records:
            aa_by_cg[r.caregiver_id].append(r)
        caregivers = _get_caregivers_with_data(current_month.year, current_month.month, db)
        cg_map = {cg.id: cg for cg in caregivers}
        aa_results = []
        for cg_id, records in aa_by_cg.items():
            cg = cg_map.get(cg_id)
            if not cg:
                cg = db.query(User).filter(User.id == cg_id).first()
            total = sum(r.caregiver_share for r in records)
            # 依個案分組（內含 AA 碼細項）
            by_case = []
            case_groups = defaultdict(list)
            for r in records:
                case_groups[r.case_id].append(r)
            for case_id, recs in case_groups.items():
                case = recs[0].case
                by_code = defaultdict(lambda: {"count": 0, "total": 0})
                for rec in recs:
                    by_code[rec.aa_code]["count"] += 1
                    by_code[rec.aa_code]["total"] += rec.caregiver_share
                by_case.append({
                    "case": case,
                    "by_code": dict(by_code),
                })
            # 依個案名稱排序
            by_case.sort(key=lambda x: x["case"].name if x["case"] else "")
            # 依 AA 碼分組（跨個案彙總）
            by_code_global = defaultdict(lambda: {"count": 0, "total": 0})
            for r in records:
                by_code_global[r.aa_code]["count"] += 1
                by_code_global[r.aa_code]["total"] += r.caregiver_share
            aa_results.append({
                "caregiver": cg,
                "total": total,
                "records": records,
                "by_code": dict(by_code_global),
                "by_case": by_case,
            })
        aa_results.sort(key=lambda x: x["caregiver"].display_name if x["caregiver"] else "")
        # 找出當月有 AA06 但尚未設定條件的個案
        pending_aa06_cases = _get_pending_aa06_cases(db, current_month.year, current_month.month)
        # 當月已上傳的檔案類型
        existing_sources = db.query(AaCodeRecord.source_file).filter(
            AaCodeRecord.year == current_month.year,
            AaCodeRecord.month == current_month.month,
        ).distinct().all()
        uploaded_types = set()
        for (sf,) in existing_sources:
            if sf and sf.startswith("["):
                stype = sf[1:sf.index("]")]
                uploaded_types.add(stype)
        return templates.TemplateResponse(
            request, "transport_salary.html", {
                "user": user, "tab": tab,
                "month_options": _month_options(db),
                "current_month": current_month,
                "month_display": _month_display(current_month),
                "aa_results": aa_results,
                "excluded_codes": sorted(EXCLUDED_AA_CODES),
                "transfer_stats": {"total": 0, "ok": 0, "failed": 0, "pending": 0},
                "error": error, "success": request.query_params.get("success", ""),
                "has_import_data": False,
                "earnings_items": [], "extra_earnings_items": [], "deductions_items": [],
                "lt_item_id": None, "today": date.today(),
                "aa_detail_json": {},
                "pending_aa06_cases": pending_aa06_cases,
                "uploaded_types": uploaded_types,
                "aa_ref_month_display": aa_ref_month_display,
            }
        )

    # ── 差旅油資分頁 ──────────────────────────────────────────────────────
    if tab == "travel_allowance":
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
        results = []
        for cg in caregivers:
            s = salary_map.get(cg.id)
            if s:
                results.append({"caregiver": cg, "salary": s})
        results.sort(key=lambda x: x["caregiver"].display_name or "")
        return templates.TemplateResponse(
            request, "transport_salary.html", {
                "user": user, "tab": tab,
                "month_options": _month_options(db),
                "current_month": current_month,
                "month_display": _month_display(current_month),
                "results": results,
                "transfer_stats": {"total": 0, "ok": 0, "failed": 0, "pending": 0},
                "error": error, "success": "",
                "has_import_data": False,
                "earnings_items": [], "extra_earnings_items": [], "deductions_items": [],
                "lt_item_id": None, "today": date.today(),
                "aa_detail_json": {},
                "uploaded_types": set(),
                "aa_ref_month_display": aa_ref_month_display,
            }
        )

    # ── 其他分頁 stub ──────────────────────────────────────────────────────
    if tab in ("year_end_bonus", "incentive_bonus", "performance_bonus"):
        return templates.TemplateResponse(
            request, "transport_salary.html", {
                "user": user, "tab": tab,
                "month_options": _month_options(db),
                "current_month": current_month,
                "month_display": _month_display(current_month),
                "results": [],
                "transfer_stats": {"total": 0, "ok": 0, "failed": 0, "pending": 0},
                "error": error, "success": "",
                "has_import_data": False,
                "earnings_items": [], "extra_earnings_items": [], "deductions_items": [],
                "lt_item_id": None, "today": date.today(),
                "aa_detail_json": {},
                "uploaded_types": set(),
                "aa_ref_month_display": aa_ref_month_display,
            }
        )

    # ── 薪資預設分頁 ──────────────────────────────────────────────────────
    caregivers = _get_caregivers_with_data(current_month.year, current_month.month, db)
    transfer_stats = _get_transfer_stats(current_month.year, current_month.month, db)
    has_import = _has_import_data(current_month.year, current_month.month, db)

    salary_records = (
        db.query(MonthlySalary)
        .filter(
            MonthlySalary.year == current_month.year,
            MonthlySalary.month == current_month.month,
        )
        .all()
    )
    salary_map = {s.caregiver_id: s for s in salary_records}

    items = db.query(SalaryItem).order_by(SalaryItem.category, SalaryItem.display_order).all()
    earnings_items = [it for it in items if it.category == "earnings"]
    deductions_items = [it for it in items if it.category == "deductions"]
    extra_earnings_items = [it for it in earnings_items if it.name not in MS_MAP]

    lt_item = db.query(SalaryItem).filter(SalaryItem.name == "久任獎金").first()
    lt_item_id = lt_item.id if lt_item else None

    all_payments = (
        db.query(SalaryPayment)
        .filter(
            SalaryPayment.year == current_month.year,
            SalaryPayment.month == current_month.month,
        )
        .all()
    )
    pay_by_cg: dict[str, dict[int, SalaryPayment]] = {}
    for p in all_payments:
        pay_by_cg.setdefault(p.caregiver_id, {})[p.salary_item_id] = p

    # ── AA 碼個案明細（給薪資分頁點選展開） ──
    # 薪資頁 AA 獎金來自前一個月：6 月薪資顯示 5 月的 AA 資料
    if current_month.month == 1:
        aa_q_year = current_month.year - 1
        aa_q_month = 12
    else:
        aa_q_year = current_month.year
        aa_q_month = current_month.month - 1
    aa_ref_month_display = f"{aa_q_year}年{aa_q_month}月"
    aa_records_for_salary = db.query(AaCodeRecord).filter(
        AaCodeRecord.year == aa_q_year,
        AaCodeRecord.month == aa_q_month,
    ).all()
    aa_case_detail: dict[str, list[dict]] = {}
    for rec in aa_records_for_salary:
        aa_case_detail.setdefault(rec.caregiver_id, []).append(rec)
    # 整理成同個案合併的格式
    aa_detail_json = {}
    for cg_id, recs in aa_case_detail.items():
        case_groups: dict[str, list] = {}
        for r in recs:
            case_groups.setdefault(r.case_id, []).append(r)
        entries = []
        for case_id, group in case_groups.items():
            case = group[0].case
            by_code = defaultdict(lambda: {"count": 0, "total": 0})
            for r in group:
                by_code[r.aa_code]["count"] += 1
                by_code[r.aa_code]["total"] += r.caregiver_share
            total = sum(r.caregiver_share for r in group)
            entries.append({
                "case_name": case.name if case else "（已刪除）",
                "total": total,
                "codes": [{"code": k, "count": v["count"], "total": v["total"]} for k, v in by_code.items()],
            })
        entries.sort(key=lambda x: x["case_name"])
        aa_detail_json[cg_id] = entries

    results = []
    for cg in caregivers:
        ms = salary_map.get(cg.id)
        payments = pay_by_cg.get(cg.id, {})

        earnings = {}
        for ei in earnings_items:
            val = None
            if ms and ei.name in MS_MAP:
                val = getattr(ms, MS_MAP[ei.name], None)
            if ei.id in payments:
                val = payments[ei.id].amount
            earnings[ei.id] = val

        deductions = {}
        for di in deductions_items:
            val = None
            if di.id in payments:
                val = payments[di.id].amount
            deductions[di.id] = val

        earnings_total = sum(v or 0 for v in earnings.values())
        # 薪資分頁不包含久任獎金
        salary_earnings_total = earnings_total
        if lt_item_id and lt_item_id in earnings and earnings[lt_item_id]:
            salary_earnings_total -= (earnings[lt_item_id] or 0)

        deductions_total = sum(v or 0 for v in deductions.values())

        results.append({
            "caregiver": cg,
            "salary": ms,
            "earnings": earnings,
            "deductions": deductions,
            "earnings_total": earnings_total,
            "salary_earnings_total": salary_earnings_total,
            "deductions_total": deductions_total,
            "net_pay": salary_earnings_total - deductions_total,
        })

    # ── AA06 待設定條件（給薪資分頁也顯示提醒） ──
    pending_aa06_cases = _get_pending_aa06_cases(db, current_month.year, current_month.month)

    return templates.TemplateResponse(
            request, "transport_salary.html", {
                "user": user,
                "tab": tab,
                "month_options": _month_options(db),
                "current_month": current_month,
                "month_display": _month_display(current_month),
                "results": results,
                "transfer_stats": transfer_stats,
                "error": error,
                "has_import_data": has_import,
                "aa_ref_month_display": aa_ref_month_display,
                "earnings_items": earnings_items,
                "extra_earnings_items": extra_earnings_items,
                "deductions_items": deductions_items,
                "lt_item_id": lt_item_id,
                "today": date.today(),
                "aa_detail_json": aa_detail_json,
                "aa_ref_month_display": aa_ref_month_display,
                "pending_aa06_cases": pending_aa06_cases,
                "uploaded_types": set(),
            }
        )


# ── 久任獎金分頁 ──────────────────────────────────────────────────────────

def _handle_long_term_bonus(request, current_month, db, user):
    lt_item = db.query(SalaryItem).filter(SalaryItem.name == "久任獎金").first()
    lt_item_id = lt_item.id if lt_item else None

    salary_rows = (
        db.query(MonthlySalary)
        .filter(MonthlySalary.long_term_bonus > 0)
        .order_by(MonthlySalary.caregiver_id, MonthlySalary.year, MonthlySalary.month)
        .all()
    )

    by_cg = defaultdict(list)
    all_months = set()
    for r in salary_rows:
        by_cg[r.caregiver_id].append(r)
        all_months.add((r.year, r.month))

    cg_ids = list(by_cg.keys())
    caregivers = db.query(User).filter(User.id.in_(cg_ids)).all() if cg_ids else []
    cg_map = {u.id: u for u in caregivers}

    # 已發放（payment_date 有值）
    released_set = set()
    if lt_item:
        sps = (
            db.query(SalaryPayment)
            .filter(
                SalaryPayment.salary_item_id == lt_item.id,
                SalaryPayment.payment_date.isnot(None),
            )
            .all()
        )
        for sp in sps:
            released_set.add((sp.caregiver_id, sp.year, sp.month))

    month_columns = sorted(all_months)
    month_labels = [f"{y}-{m:02d}" for y, m in month_columns]

    bonus_rows = []
    for cg_id, records in by_cg.items():
        cg = cg_map.get(cg_id)
        if not cg:
            continue

        record_map = {(r.year, r.month): r.long_term_bonus for r in records}
        total = sum(v or 0 for v in record_map.values())
        pending_total = 0
        pending_cells = {}
        for ym, val in record_map.items():
            if ym in released_set:
                continue
            pending_total += (val or 0)
            pending_cells[ym] = val

        bonus_rows.append({
            "caregiver": cg,
            "total": total,
            "pending_total": pending_total,
            "pending_cells": pending_cells,
        })

    month_options = sorted(
        set(
            (r.year, r.month)
            for rec in salary_rows
            for r in [rec]
        ),
        reverse=True,
    )

    return templates.TemplateResponse(
        request, "transport_salary.html", {
            "user": user, "tab": "long_term_bonus",
            "month_options": _month_options(db),
            "current_month": current_month,
            "month_display": _month_display(current_month),
            "bonus_rows": bonus_rows,
            "month_columns": month_columns,
            "month_labels": month_labels,
            "lt_item_id": lt_item_id,
            "transfer_stats": {"total": 0, "ok": 0, "failed": 0, "pending": 0},
            "error": None, "has_import_data": False,
            "earnings_items": [], "extra_earnings_items": [], "deductions_items": [],
            "results": [], "today": date.today(),
        }
    )


# ── 執行久任獎金發放 ─────────────────────────────────────────────────────

@router.post("/release-long-term-bonus")
def release_long_term_bonus(
    request: Request,
    date_from: str = Form(...),
    date_to: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director, UserRole.accountant)),
):
    """發放區間內所有在職居服員的久任獎金"""
    try:
        from_date = datetime.strptime(date_from, "%Y-%m").date().replace(day=1)
        to_date = datetime.strptime(date_to, "%Y-%m").date().replace(day=1)
    except ValueError:
        return RedirectResponse(
            url=f"/transport-salary?tab=long_term_bonus&error=日期格式錯誤",
            status_code=302,
        )

    lt_item = db.query(SalaryItem).filter(SalaryItem.name == "久任獎金").first()
    if not lt_item:
        return RedirectResponse(
            url="/transport-salary?tab=long_term_bonus&error=找不到久任獎金項目",
            status_code=302,
        )

    satisfied = []
    skipped_resigned = []

    for y in range(from_date.year, to_date.year + 1):
        for m in range(1, 13):
            if (y == from_date.year and m < from_date.month) or (y == to_date.year and m > to_date.month):
                continue
            rows = (
                db.query(MonthlySalary)
                .filter(
                    MonthlySalary.year == y,
                    MonthlySalary.month == m,
                    MonthlySalary.long_term_bonus > 0,
                )
                .all()
            )
            for r in rows:
                cg = db.query(User).filter(User.id == r.caregiver_id).first()
                if not cg:
                    continue
                # 檢查是否已發放
                existing = (
                    db.query(SalaryPayment)
                    .filter(
                        SalaryPayment.caregiver_id == r.caregiver_id,
                        SalaryPayment.year == y,
                        SalaryPayment.month == m,
                        SalaryPayment.salary_item_id == lt_item.id,
                        SalaryPayment.payment_date.isnot(None),
                    )
                    .first()
                )
                if existing:
                    continue
                # 離職日在發放日前 → 不發給
                if cg.termination_date and cg.termination_date <= date.today():
                    skipped_resigned.append(f"{cg.display_name} ({y}-{m:02d})")
                    continue

                sp = SalaryPayment(
                    caregiver_id=r.caregiver_id,
                    year=y,
                    month=m,
                    salary_item_id=lt_item.id,
                    amount=int(r.long_term_bonus or 0),
                    payment_date=date.today(),
                    notes=f"自動發放（區間 {date_from} ~ {date_to}）",
                )
                db.add(sp)
                satisfied.append(f"{cg.display_name} ({y}-{m:02d}): {int(r.long_term_bonus)} 元")

    db.commit()

    parts = []
    if satisfied:
        parts.append(f"已發放 {len(satisfied)} 筆")
    if skipped_resigned:
        parts.append(f"已離職不發放 {len(skipped_resigned)} 筆")
    msg = "，".join(parts) if parts else "無符合條件的久任獎金"
    return RedirectResponse(
        url=f"/transport-salary?tab=long_term_bonus&success={msg}",
        status_code=302,
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

    # 檢查是否有正在執行的計算
    for job in list(_jobs.values()):
        if job.get("status") == "running":
            return RedirectResponse(
                url=f"/transport-salary?month={month}&error=已有計算正在執行中", status_code=302
            )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "message": "計算排程中...", "created_at": datetime.utcnow().isoformat()}

    def _run(jid: str, y: int, m: int):
        from app.database import SessionLocal
        bg_db = SessionLocal()
        try:
            _jobs[jid]["message"] = "正在查詢 Google Maps API..."
            stats = calculate_all_transfers(y, m, bg_db)
            bg_db.commit()
            msg = (
                f"轉場計算完成：{stats['caregivers']} 位居服員，"
                f"成功 {stats['ok']} 筆、快取 {stats['cached']} 筆、"
                f"跳過 {stats['skipped']} 筆、失敗 {stats['failed']} 筆"
            )
            _jobs[jid].update({"status": "done", "stats": stats, "message": msg})
        except Exception as e:
            bg_db.rollback()
            _jobs[jid].update({"status": "error", "message": f"計算失敗：{e}"})
        finally:
            bg_db.close()

    t = threading.Thread(target=_run, args=(job_id, year, mon), daemon=True)
    t.start()

    return RedirectResponse(
        url=f"/transport-salary?month={month}&job_id={job_id}",
        status_code=302,
    )


# ── 執行薪資試算 ──────────────────────────────────────────────────────────

@router.get("/calculate-status/{job_id}")
def get_calculate_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    return {
        "status": job["status"],
        "message": job.get("message", ""),
        "stats": job.get("stats"),
    }


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


# ── 上傳時薪明細表（更新系統班表） ─────────────────────────────────────────

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

    # 先刪除該月既有 CaregiverServiceRecord（重新匯入取代）
    month_start = current_month
    if current_month.month == 12:
        month_end = date(current_month.year, 12, 31)
    else:
        month_end = date(current_month.year, current_month.month + 1, 1) - timedelta(days=1)
    db.query(CaregiverServiceRecord).filter(
        CaregiverServiceRecord.service_date >= month_start,
        CaregiverServiceRecord.service_date <= month_end,
    ).delete(synchronize_session=False)

    from datetime import time as dtime
    _visit_counter: dict = {}
    imported = 0
    skipped_no_cg = 0
    skipped_no_case = 0
    skipped_no_date = 0

    for _, row in df.iterrows():
        cg_name = str(row["員工姓名"]).strip()
        case_name = str(row[case_col]).strip()

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

        # 從 bracket 欄位加總服務分鐘
        total_min = 0
        for col_idx, (dt, bracket) in BRACKET_COL_INDEX.items():
            if col_idx < len(df.columns):
                col_name = str(df.columns[col_idx])
                val = int(pd.to_numeric(row.get(col_name, 0) or 0, errors="coerce") or 0)
                total_min += val
        # 補滿分鐘
        fill_col = None
        for c in df.columns:
            if "補滿服務分鐘" in str(c) or "補滿" in str(c):
                fill_col = str(c)
                break
        if fill_col:
            fill_min = int(row.get(fill_col, 0) or 0)
            total_min += fill_min

        if total_min <= 0:
            continue

        # 用 visit_order 計算起迄時間
        base_hour = 8 + (visit_order - 1) * 3  # 第1筆 08:00, 第2筆 11:00, 第3筆 14:00...
        start_time = dtime(min(base_hour, 23), 0)
        end_minutes = start_time.hour * 60 + total_min
        end_hour = end_minutes // 60
        end_min = end_minutes % 60
        end_time = dtime(min(end_hour, 23), min(end_min, 59))

        db.add(CaregiverServiceRecord(
            case_id=case_id,
            caregiver_id=caregiver_id,
            service_date=svc_date,
            start_time=start_time,
            end_time=end_time,
            minutes=total_min,
            case_name_raw=case_name,
            caregiver_name_raw=cg_name,
            service_codes="",
            formalization_status="external_import",
            source_file=filename,
        ))
        imported += 1

    db.commit()

    parts = [f"已將 {imported} 筆實際服務更新至系統班表"]
    if skipped_no_cg:
        parts.append(f"跳過（無對應居服員）{skipped_no_cg}")
    if skipped_no_case:
        parts.append(f"跳過（無對應個案）{skipped_no_case}")
    if skipped_no_date:
        parts.append(f"跳過（無日期）{skipped_no_date}")
    if imported > 0:
        parts.append("請重新執行「① 計算轉場」→「② 試算薪資」→「試算差旅油資」")

    return RedirectResponse(
        url=f"/transport-salary?month={month}&success={'，'.join(parts)}",
        status_code=302
    )


# ── 薪資單 ────────────────────────────────────────────────────────────────

@router.get("/salary-slip/{caregiver_id}", response_class=HTMLResponse)
def salary_slip(
    request: Request,
    caregiver_id: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director, UserRole.accountant, UserRole.manager, UserRole.caregiver)),
):
    current_month = _parse_month(month, db)
    caregiver = db.query(User).filter(User.id == caregiver_id).first()
    if not caregiver:
        return RedirectResponse(url="/transport-salary", status_code=302)

    # 居服員只能看自己的薪資單
    if user.role == UserRole.caregiver and user.id != caregiver_id:
        return RedirectResponse(url="/transport-salary", status_code=302)

    ms = db.query(MonthlySalary).filter(
        MonthlySalary.caregiver_id == caregiver_id,
        MonthlySalary.year == current_month.year,
        MonthlySalary.month == current_month.month,
    ).first()

    items = db.query(SalaryItem).order_by(SalaryItem.category, SalaryItem.display_order).all()
    earnings_items = [it for it in items if it.category == "earnings"]
    deductions_items = [it for it in items if it.category == "deductions"]

    payments = {
        p.salary_item_id: p
        for p in db.query(SalaryPayment).filter(
            SalaryPayment.caregiver_id == caregiver_id,
            SalaryPayment.year == current_month.year,
            SalaryPayment.month == current_month.month,
        ).all()
    }

    MS_MAP = {
        "本薪（含交通）": "salary_with_transport",
        "交通津貼": "transport_allowance",
        "久任獎金": "long_term_bonus",
        "AA碼獎金": "aa_bonus",
    }

    slip_earnings = []
    for ei in earnings_items:
        val = None
        if ms and ei.name in MS_MAP:
            val = getattr(ms, MS_MAP[ei.name], None)
        if ei.id in payments:
            val = payments[ei.id].amount
        if val:
            slip_earnings.append({"name": ei.name, "amount": val})

    slip_deductions = []
    for di in deductions_items:
        val = None
        if di.id in payments:
            val = payments[di.id].amount
        if val:
            slip_deductions.append({"name": di.name, "amount": val})

    earnings_total = sum(v["amount"] or 0 for v in slip_earnings)
    deductions_total = sum(v["amount"] or 0 for v in slip_deductions)
    net_pay = earnings_total - deductions_total

    return templates.TemplateResponse(
        request, "salary_slip.html", {
            "user": user,
            "caregiver": caregiver,
            "current_month": current_month,
            "month_display": _month_display(current_month),
            "ms": ms,
            "slip_earnings": slip_earnings,
            "slip_deductions": slip_deductions,
            "earnings_total": earnings_total,
            "deductions_total": deductions_total,
            "net_pay": net_pay,
        }
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
    current_month = _parse_month(month, db)
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
    detail_by_date = {dd["date"]: dd for dd in daily_details}
    for day in range(1, days_in_month + 1):
        d = date(current_month.year, current_month.month, day)
        dd = _get_daily_visit_details(caregiver_id, d, db)
        if dd:
            daily_visits.append({
                "date": d,
                "visits": dd["visits"],
                "transfers": dd["transfers"],
                "detail": detail_by_date.get(d),
            })

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
    ascii_name = f"salary_{current_month.year}-{current_month.month:02d}.xlsx"
    filename_header = f"attachment; filename={ascii_name}; filename*=UTF-8''{quote(f'交通津貼薪資_{current_month.year}-{current_month.month:02d}.xlsx')}"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": filename_header},
    )


# ── AA 碼清冊匯入 ──────────────────────────────────────────────────────────

@router.post("/import-aa-codes")
def import_aa_codes(
    request: Request,
    month: str = Form(...),
    file: UploadFile = File(...),
    file_type: str = Form("居家服務+喘息"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director, UserRole.accountant)),
):
    try:
        current_month = _parse_month(month)
        if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
            return RedirectResponse(
                url=f"/transport-salary?tab=aa_bonus&month={month}&error=請上傳 Excel 檔案",
                status_code=302,
            )
        temp_dir = "data"
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"aa_import_{uuid.uuid4().hex}_{file.filename}")
        try:
            raw = file.file.read()
            if not raw:
                raw = file.read()
            with open(temp_path, "wb") as f:
                f.write(raw)
        except Exception as e:
            err_detail = f"{type(e).__name__}: {e}"
            try: os.remove(temp_path)
            except OSError: pass
            return RedirectResponse(
                url=f"/transport-salary?tab=aa_bonus&month={month}&error=讀取檔案失敗：{quote(err_detail)}",
                status_code=302,
            )

        try:
            result = import_aa_file(db, temp_path, source_label=file.filename, source_type=file_type,
                                    target_year=current_month.year, target_month=current_month.month)
        except Exception as e:
            err_detail = f"{type(e).__name__}: {e}"
            try: os.remove(temp_path)
            except OSError: pass
            return RedirectResponse(
                url=f"/transport-salary?tab=aa_bonus&month={month}&error=匯入解析失敗：{quote(err_detail)}",
                status_code=302,
            )
        stats = result["stats"]
        allocations = result["allocations"]

        pending_aa06 = stats.get("pending_aa06", {})
        pending_count = len(pending_aa06)

        if allocations:
            try:
                save_result = save_allocations(db, allocations, current_month.year, current_month.month, source_type=file_type)
            except Exception as e:
                err_detail = f"{type(e).__name__}: {e}"
                return RedirectResponse(
                    url=f"/transport-salary?tab=aa_bonus&month={month}&error=儲存分配失敗：{quote(err_detail)}",
                    status_code=302,
                )
        else:
            save_result = {"total_cg": 0, "total_records": 0}

        # 清理暫存檔
        try:
            os.remove(temp_path)
        except OSError:
            pass

        msg_parts = [
            f"匯入完成：共 {stats['total']} 筆，跳過 {stats['skipped']} 筆（AA01/02/08/09），"
            f"分配 {stats['allocated']} 筆予 {save_result['total_cg']} 位居服員",
        ]
        if stats["errors"]:
            msg_parts.append(f"（{len(stats['errors'])} 個錯誤）")
        if pending_count:
            msg_parts.append(f"，{pending_count} 個 AA06 個案待設定條件（暫未分配）")

        return RedirectResponse(
            url=f"/transport-salary?tab=aa_bonus&month={month}&success={quote('；'.join(msg_parts))}",
            status_code=302,
        )
    except Exception as e:
        err_detail = f"{type(e).__name__}: {e}"
        return RedirectResponse(
            url=f"/transport-salary?tab=aa_bonus&month={month}&error=匯入失敗：{quote(err_detail)}",
            status_code=302,
        )


@router.get("/aa06-conditions")
def aa06_conditions_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director, UserRole.accountant)),
):
    """列出所有已設定條件的 AA06 個案"""
    all_conds = db.query(Aa06CaseCondition).all()
    condition_options = {
        1: "管路/傷口/燒燙傷，或移位困難且體重>70KG，提供 BA01 或 BA07",
        2: "ADL 移位或上下樓梯完全依賴，需 2 人以上提供 BA12",
        3: "ADL 移位可自行坐起但離床需協助，且體重>70KG，提供 BA12",
        4: "12 歲以下（含）提供 BA01、BA02 或 BA07",
    }
    cases_with_conditions = []
    for c in all_conds:
        case = db.query(Case).filter(Case.id == c.case_id).first()
        conds = [int(x) for x in c.conditions.split(",") if x.strip()]
        cases_with_conditions.append({
            "case_name": case.name if case else "（已刪除）",
            "case_id": c.case_id,
            "conditions": [{"num": n, "desc": condition_options.get(n, "")} for n in conds],
        })
    cases_with_conditions.sort(key=lambda x: x["case_name"])
    return templates.TemplateResponse(
        request, "aa06_conditions.html", {
            "user": user,
            "case": None,
            "current_conditions": [],
            "cases_with_conditions": cases_with_conditions,
            "condition_options": [
                {"num": 1, "desc": condition_options[1]},
                {"num": 2, "desc": condition_options[2]},
                {"num": 3, "desc": condition_options[3]},
                {"num": 4, "desc": condition_options[4]},
            ],
        }
    )


@router.get("/aa06-conditions/{case_id}")
def aa06_conditions_page(
    request: Request,
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director, UserRole.accountant)),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/transport-salary?tab=aa_bonus", status_code=302)
    cond = db.query(Aa06CaseCondition).filter(Aa06CaseCondition.case_id == case_id).first()
    current_conditions = []
    if cond:
        current_conditions = [int(x) for x in cond.conditions.split(",") if x.strip()]
    return templates.TemplateResponse(
        request, "aa06_conditions.html", {
            "user": user, "case": case,
            "current_conditions": current_conditions,
            "condition_options": [
                {"num": 1, "desc": "管路/傷口/燒燙傷，或移位困難且體重>70KG，提供 BA01 或 BA07"},
                {"num": 2, "desc": "ADL 移位或上下樓梯完全依賴，需 2 人以上提供 BA12"},
                {"num": 3, "desc": "ADL 移位可自行坐起但離床需協助，且體重>70KG，提供 BA12"},
                {"num": 4, "desc": "12 歲以下（含）提供 BA01、BA02 或 BA07"},
            ],
        }
    )


@router.post("/aa06-conditions/{case_id}")
def aa06_conditions_save(
    request: Request,
    case_id: str,
    conditions: list[str] = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director, UserRole.accountant)),
):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/transport-salary?tab=aa_bonus", status_code=302)
    cond_str = ",".join(conditions)
    cond = db.query(Aa06CaseCondition).filter(Aa06CaseCondition.case_id == case_id).first()
    if cond:
        cond.conditions = cond_str
    else:
        db.add(Aa06CaseCondition(case_id=case_id, conditions=cond_str))
    db.commit()
    return RedirectResponse(
        url=f"/transport-salary?tab=aa_bonus&month={request.query_params.get('month', '')}&success=AA06 條件已儲存",
        status_code=302,
    )


# ── 差旅油資計算 ──────────────────────────────────────────────────────────

@router.post("/calculate-travel-allowance")
def calculate_travel_allowance(
    month: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director, UserRole.accountant)),
):
    import calendar
    current_month = _parse_month(month)
    _, days_in_month = calendar.monthrange(current_month.year, current_month.month)
    month_start = date(current_month.year, current_month.month, 1)
    month_end = date(current_month.year, current_month.month, days_in_month)

    # 從 CaregiverTransfer 加總每位居服員當月里程
    rows = (
        db.query(
            CaregiverTransfer.caregiver_id,
            sa_func.sum(CaregiverTransfer.transfer_km).label("total_km"),
        )
        .filter(
            CaregiverTransfer.service_date >= month_start,
            CaregiverTransfer.service_date <= month_end,
        )
        .group_by(CaregiverTransfer.caregiver_id)
        .all()
    )
    count = 0
    for cg_id, total_km in rows:
        if not total_km:
            continue
        total_km = float(total_km)
        ms = db.query(MonthlySalary).filter(
            MonthlySalary.caregiver_id == cg_id,
            MonthlySalary.year == current_month.year,
            MonthlySalary.month == current_month.month,
        ).first()
        if ms:
            ms.total_transfer_km = round(total_km, 2)
            ms.travel_allowance = calc_travel_allowance(total_km)
            count += 1
    db.commit()
    return RedirectResponse(
        url=f"/transport-salary?tab=travel_allowance&month={month}&success=差旅油資計算完成，共 {count} 人",
        status_code=302,
    )
