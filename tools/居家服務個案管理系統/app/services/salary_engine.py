import json
import math
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.caregiver_transfer import CaregiverTransfer
from app.models.case import Case
from app.models.monthly_salary import MonthlySalary
from app.models.user import User, UserRole
from app.services.attendance_engine import (
    DATE_TYPE_LABELS,
    OVERTIME_MULTIPLIERS,
    BRACKET_CAPS_MINUTES,
    classify_date_from_records,
    get_holiday_dates_for_year,
)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_PATH = CACHE_DIR / "distance_cache.json"
_cache = None

LONG_TERM_BONUS_TABLE = {
    "新人_滿5年": [(151, float("inf"), 1600), (121, 150, 1100), (90, 120, 900), (60, 89, 700)],
    "新人_滿3年": [(151, float("inf"), 1300), (121, 150, 900),  (90, 120, 700), (60, 89, 500)],
    "新人_滿2年": [(151, float("inf"), 1000), (121, 150, 700),  (90, 120, 500), (60, 89, 300)],
    "新人_滿年":  [(151, float("inf"), 700),  (121, 150, 500),  (90, 120, 300), (60, 89, 100)],
    "舊人_滿5年": [(151, float("inf"), 2000), (121, 150, 1600), (90, 120, 1100), (60, 89, 900)],
    "舊人_滿3年": [(151, float("inf"), 1600), (121, 150, 1300), (90, 120, 900),  (60, 89, 700)],
    "舊人_滿2年": [(151, float("inf"), 1300), (121, 150, 1000), (90, 120, 700),  (60, 89, 500)],
    "舊人_滿年":  [(151, float("inf"), 1000), (121, 150, 700),  (90, 120, 500),  (60, 89, 300)],
}

NEW_EMPLOYEES = {"馬富玲", "鄭淑珍", "張家涵", "黃雅君", "李郁萱", "陳宥蓁", "黃誌輝", "林宜臻"}
OLD_EMPLOYEES = {
    "鄭翰靖", "陳偉盛", "楊沛紘", "張美郁", "謝金燕", "吳綋珏", "張峯銘",
    "曾永成", "劉素貞", "楊玉玲", "張文華", "邱碧娥", "蔡旻宏", "潘玉枝",
    "王培軒", "古瑞卯",
}

from app.config import settings
from app.services.insurance import (
    calc_labor_insurance_self_pay,
    calc_health_insurance_self_pay,
    calc_labor_pension_self_pay,
)
GOOGLE_MAPS_API_KEY = settings.google_maps_api_key


def check_api_key_valid() -> tuple[bool, str]:
    if not GOOGLE_MAPS_API_KEY:
        return False, "未設定 GOOGLE_MAPS_API_KEY"
    try:
        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        params = {"origins": "新北市板橋區中山路一段1號", "destinations": "新北市板橋區中山路一段2號", "key": GOOGLE_MAPS_API_KEY, "language": "zh-TW", "mode": "driving", "region": "tw"}
        r = requests.get(url, params=params, timeout=10)
        j = r.json()
        if j.get("status") == "OK":
            return True, ""
        err = j.get("error_message", j.get("status", "未知錯誤"))
        return False, f"Google Maps API 錯誤：{err}"
    except Exception as e:
        return False, f"Google Maps API 連線失敗：{e}"


def check_api_key_exists() -> bool:
    return bool(GOOGLE_MAPS_API_KEY)


# ── 地址清理（比照使用者腳本 2.轉場距離計算.py）───────────────

def clean_address(addr):
    """NFKC + 移除括弧 + 移除空白 + 移除鄰 + 移除樓層。
    Google Maps 查不到鄰跟樓層，所以要刪掉。
    不轉台→臺（新台五路的台是路名本體），不轉路名數字。
    """
    if not addr or not isinstance(addr, str):
        return ""
    addr = unicodedata.normalize("NFKC", addr)
    addr = re.sub(r"[（(].*?[）)]", "", addr)
    addr = re.sub(r"\s+", "", addr)
    addr = re.sub(r"\d+鄰", "", addr)
    addr = re.sub(r"[一二三四五六七八九十百千]+樓", "", addr)
    addr = re.sub(r"\d+樓[之\-]?[一二三四五六七八九十百千\d]*", "", addr)
    return addr.strip()


# ── Google Maps API（含快取） ─────────────────────────────────────────────

def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            _cache = {}
            for k, v in raw.items():
                try:
                    ori, dst = k.split("__")
                    _cache[f"{clean_address(ori)}__{clean_address(dst)}"] = v
                except Exception:
                    continue
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache():
    global _cache
    if _cache is not None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)


def get_distance_duration(origin: str, destination: str) -> tuple:
    cache = _load_cache()
    origin_c = clean_address(origin)
    dest_c = clean_address(destination)
    key = f"{origin_c}__{dest_c}"

    if not origin_c or not dest_c:
        return 0, 0, "EMPTY_ADDR", ""

    if key in cache:
        dist, dur = cache[key]
        return dist, dur, "CACHED_OK", ""

    if not GOOGLE_MAPS_API_KEY:
        return 0, 0, "NO_API_KEY", "未設定 GOOGLE_MAPS_API_KEY"

    def _call(orig, dest):
        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        params = {
            "origins": orig,
            "destinations": dest,
            "key": GOOGLE_MAPS_API_KEY,
            "language": "zh-TW",
            "mode": "driving",
            "region": "tw",
        }
        r = requests.get(url, params=params, timeout=10)
        return r.json()

    try:
        j = _call(origin_c, dest_c)
        top_status = j.get("status", "UNKNOWN")
        err_msg = j.get("error_message", "")

        if top_status == "OK":
            elem = j["rows"][0]["elements"][0]
            elem_status = elem.get("status", "UNKNOWN")
            if elem_status == "OK":
                dist = elem["distance"]["value"]
                dur = elem["duration"]["value"]
                if dist > 0 and dur > 0:
                    cache[key] = (dist, dur)
                    _save_cache()
                return dist, dur, "OK", ""

        j2 = _call(origin_c + ", 台灣", dest_c + ", 台灣")
        top_status2 = j2.get("status", "UNKNOWN")
        err_msg2 = j2.get("error_message", "")
        if top_status2 == "OK":
            elem2 = j2["rows"][0]["elements"][0]
            elem_status2 = elem2.get("status", "UNKNOWN")
            if elem_status2 == "OK":
                dist = elem2["distance"]["value"]
                dur = elem2["duration"]["value"]
                if dist > 0 and dur > 0:
                    cache[key] = (dist, dur)
                    _save_cache()
                return dist, dur, "OK_FALLBACK", ""
            else:
                return 0, 0, elem_status2, err_msg2
        else:
            return 0, 0, top_status2, err_msg2

    except Exception as e:
        return 0, 0, f"EXC:{type(e).__name__}", str(e)


# ── 轉場距離計算 ──────────────────────────────────────────────────────────

def get_case_address(case_id: str, db: Session, *, use_hospital_addr: bool = False) -> str:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        return ""
    if use_hospital_addr and case.dialysis_hospital_address:
        return clean_address(case.dialysis_hospital_address)
    return clean_address(case.residence_address or case.household_address or "")


def calculate_caregiver_transfers(
    caregiver_id: str, year: int, month: int, db: Session
) -> dict:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    records = (
        db.query(CaregiverServiceRecord)
        .filter(
            CaregiverServiceRecord.caregiver_id == caregiver_id,
            CaregiverServiceRecord.service_date >= month_start,
            CaregiverServiceRecord.service_date <= month_end,
            CaregiverServiceRecord.formalization_status != "leave",
        )
        .order_by(CaregiverServiceRecord.service_date, CaregiverServiceRecord.start_time)
        .all()
    )

    stats = {"ok": 0, "skipped": 0, "failed": 0, "cached": 0}
    grouped: dict[str, list] = {}
    for r in records:
        key = f"{r.service_date.isoformat()}"
        grouped.setdefault(key, []).append(r)

    for date_key, day_records in sorted(grouped.items()):
        svc_date = date.fromisoformat(date_key)
        # Delete old transfers for this caregiver/date
        db.query(CaregiverTransfer).filter(
            CaregiverTransfer.caregiver_id == caregiver_id,
            CaregiverTransfer.service_date == svc_date,
        ).delete()

        for i in range(len(day_records) - 1):
            curr = day_records[i]
            next_r = day_records[i + 1]

            # 判斷洗腎接送：若當前案為「送」或「送+接」，結束點在醫院
            curr_case_obj = db.query(Case).filter(Case.id == curr.case_id).first()
            next_case_obj = db.query(Case).filter(Case.id == next_r.case_id).first()
            from_hospital = bool(
                curr_case_obj and curr_case_obj.is_dialysis == "Y"
                and curr_case_obj.dialysis_direction in ("送", "送+接")
            )
            to_hospital = bool(
                next_case_obj and next_case_obj.is_dialysis == "Y"
                and next_case_obj.dialysis_direction in ("接", "送+接")
            )

            addr1 = get_case_address(curr.case_id, db, use_hospital_addr=from_hospital)
            addr2 = get_case_address(next_r.case_id, db, use_hospital_addr=to_hospital)

            clean1 = clean_address(addr1)
            clean2 = clean_address(addr2)

            if not clean1 or not clean2:
                db.add(CaregiverTransfer(
                    caregiver_id=caregiver_id,
                    service_date=svc_date,
                    from_visit_id=curr.id,
                    to_visit_id=next_r.id,
                    from_case_name=curr.case_name_raw,
                    to_case_name=next_r.case_name_raw,
                    from_address=addr1,
                    to_address=addr2,
                    transfer_km=0,
                    transfer_minutes=0,
                    status="EMPTY_ADDR",
                ))
                stats["skipped"] += 1
                continue

            if clean1 == clean2:
                db.add(CaregiverTransfer(
                    caregiver_id=caregiver_id,
                    service_date=svc_date,
                    from_visit_id=curr.id,
                    to_visit_id=next_r.id,
                    from_case_name=curr.case_name_raw,
                    to_case_name=next_r.case_name_raw,
                    from_address=addr1,
                    to_address=addr2,
                    transfer_km=0,
                    transfer_minutes=0,
                    status="SAME_ADDR",
                    calculated_at=datetime.utcnow(),
                ))
                stats["ok"] += 1
                continue

            dist, dur, status, err = get_distance_duration(addr1, addr2)
            km = round(dist / 1000, 2)
            minutes = round(km / 0.67, 2) if dist > 0 else 0
            if dist > 0 and minutes < 1:
                minutes = 1.00

            if dist > 0:
                transfer_status = "CACHED" if status.startswith("CACHED") else "OK"
                if status.startswith("CACHED"):
                    stats["cached"] += 1
                else:
                    stats["ok"] += 1
            elif status == "OK" or status.startswith("CACHED"):
                transfer_status = "OK"
                stats["ok"] += 1
            else:
                transfer_status = f"FAILED_{status}"
                stats["failed"] += 1

            db.add(CaregiverTransfer(
                caregiver_id=caregiver_id,
                service_date=svc_date,
                from_visit_id=curr.id,
                to_visit_id=next_r.id,
                from_case_name=curr.case_name_raw,
                to_case_name=next_r.case_name_raw,
                from_address=addr1,
                to_address=addr2,
                transfer_km=km,
                transfer_minutes=minutes,
                status=transfer_status,
                error_message=err if transfer_status.startswith("FAILED") else "",
                calculated_at=datetime.utcnow(),
            ))

        db.commit()
    return stats


def calculate_all_transfers(year: int, month: int, db: Session) -> dict:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    caregiver_ids = (
        db.query(CaregiverServiceRecord.caregiver_id)
        .filter(
            CaregiverServiceRecord.service_date >= month_start,
            CaregiverServiceRecord.service_date <= month_end,
            CaregiverServiceRecord.formalization_status != "leave",
        )
        .distinct()
        .all()
    )

    total_stats = {"ok": 0, "skipped": 0, "failed": 0, "cached": 0, "caregivers": 0}
    for (cg_id,) in caregiver_ids:
        stats = calculate_caregiver_transfers(cg_id, year, month, db)
        for k in total_stats:
            if k in stats:
                total_stats[k] += stats[k]
        total_stats["caregivers"] += 1

    return total_stats


# ── Google Maps 轉場計算（ImportSalaryRecord） ────────────────────────────

# ── 每日加權分鐘計算 ──────────────────────────────────────────────────────

def _get_service_records_by_date(caregiver_id: str, start: date, end: date, db: Session) -> dict:
    records = (
        db.query(CaregiverServiceRecord)
        .filter(
            CaregiverServiceRecord.caregiver_id == caregiver_id,
            CaregiverServiceRecord.service_date >= start,
            CaregiverServiceRecord.service_date <= end,
            CaregiverServiceRecord.formalization_status != "leave",
        )
        .all()
    )
    result: dict[date, list] = {}
    for r in records:
        result.setdefault(r.service_date, []).append(r)
    return result


def _distribute_minutes_to_brackets(total_minutes: int, date_type: str):
    brackets = OVERTIME_MULTIPLIERS.get(date_type, {})
    caps = BRACKET_CAPS_MINUTES.get(date_type, {})
    result = {}
    remaining = total_minutes
    for bname in brackets:  # 保持定義順序（0-8 → 9-10 → 11-12 或 0-2 → 3-8 → 9-10 → 11-12）
        cap = caps.get(bname, 999999)
        alloc = min(remaining, cap)
        if alloc > 0:
            result[bname] = alloc
        remaining -= alloc
        if remaining <= 0:
            break
    return result


def _backfill_transfer_to_brackets(bracket_mins: dict, transfer_minutes: float, date_type: str):
    """Add transport minutes to the first bracket for display."""
    if transfer_minutes <= 0:
        return bracket_mins
    brackets = OVERTIME_MULTIPLIERS.get(date_type, {})
    result = dict(bracket_mins)
    bracket_names = list(brackets.keys())
    if bracket_names:
        first = bracket_names[0]
        result[first] = result.get(first, 0) + transfer_minutes
    return result


# Bracket groups matching original script's group_blocks order.
# The original script searches these groups in order,
# checking each group from last bracket to first.
_BRACKET_GROUPS = [
    ["weekday_0_8", "weekday_9_10", "weekday_11_12"],
    ["national_holiday_0_8", "national_holiday_9_10", "national_holiday_11_12"],
    ["regular_off_0_2", "regular_off_3_8", "regular_off_9_10", "regular_off_11_12"],
    ["rest_day_0_2", "rest_day_3_8", "rest_day_9_10", "rest_day_11_12"],
]

_BRACKET_DB_TO_DT_AND_BNAME = {
    "weekday_0_8": ("weekday", "0-8"),
    "weekday_9_10": ("weekday", "9-10"),
    "weekday_11_12": ("weekday", "11-12"),
    "national_holiday_0_8": ("national_holiday", "0-8"),
    "national_holiday_9_10": ("national_holiday", "9-10"),
    "national_holiday_11_12": ("national_holiday", "11-12"),
    "regular_off_0_2": ("regular_off", "0-2"),
    "regular_off_3_8": ("regular_off", "3-8"),
    "regular_off_9_10": ("regular_off", "9-10"),
    "regular_off_11_12": ("regular_off", "11-12"),
    "rest_day_0_2": ("rest_day", "0-2"),
    "rest_day_3_8": ("rest_day", "3-8"),
    "rest_day_9_10": ("rest_day", "9-10"),
    "rest_day_11_12": ("rest_day", "11-12"),
}

_ALL_BRACKET_DB_COLS = [col for grp in _BRACKET_GROUPS for col in grp]


def _calc_weighted(bracket_mins: dict, date_type: str) -> float:
    brackets = OVERTIME_MULTIPLIERS.get(date_type, {})
    return sum(bracket_mins.get(b, 0) * brackets.get(b, 1.0) for b in brackets.keys())


def get_daily_bracket_breakdown(
    caregiver_id: str, d: date, db: Session, svc_records: dict = None, holiday_dates: set = None
) -> dict:
    caregiver = db.query(User).filter(User.id == caregiver_id).first()
    if not caregiver:
        return None

    if holiday_dates is None:
        holiday_dates = set(get_holiday_dates_for_year(db, d.year).keys())

    if svc_records is None:
        range_start = d - timedelta(days=7)
        range_end = d + timedelta(days=7)
        svc_records = _get_service_records_by_date(caregiver_id, range_start, range_end, db)

    date_type, _ = classify_date_from_records(caregiver, d, holiday_dates, svc_records, db, apply_rule_4_1=False)
    day_records = svc_records.get(d, [])
    total_minutes = sum(r.minutes for r in day_records)

    if date_type == "regular_off" or total_minutes == 0:
        transfers = db.query(CaregiverTransfer).filter(
            CaregiverTransfer.caregiver_id == caregiver_id,
            CaregiverTransfer.service_date == d,
        ).all()
        return {
            "date": d, "date_type": date_type,
            "date_type_label": DATE_TYPE_LABELS.get(date_type, date_type),
            "total_minutes": total_minutes, "transfer_minutes": sum(t.transfer_minutes for t in transfers),
            "transfer_km": sum(t.transfer_km for t in transfers),
            "brackets_no_transport": {}, "brackets_with_transport": {},
            "weighted_no_transport": 0.0, "weighted_with_transport": 0.0,
        }

    bracket_no = _distribute_minutes_to_brackets(total_minutes, date_type)
    weighted_no = _calc_weighted(bracket_no, date_type)

    day_transfer_mins = 0
    day_transfer_km = 0
    transfers = db.query(CaregiverTransfer).filter(
        CaregiverTransfer.caregiver_id == caregiver_id,
        CaregiverTransfer.service_date == d,
        CaregiverTransfer.status.in_(["OK", "SAME_ADDR", "CACHED"]),
    ).all()
    for t in transfers:
        day_transfer_mins += t.transfer_minutes
        day_transfer_km += t.transfer_km

    bracket_with = _backfill_transfer_to_brackets(bracket_no, day_transfer_mins, date_type)
    weighted_with = _calc_weighted(bracket_with, date_type)

    return {
        "date": d, "date_type": date_type,
        "date_type_label": DATE_TYPE_LABELS.get(date_type, date_type),
        "total_minutes": total_minutes,
        "transfer_minutes": day_transfer_mins,
        "transfer_km": day_transfer_km,
        "brackets_no_transport": bracket_no,
        "brackets_with_transport": bracket_with,
        "weighted_no_transport": round(weighted_no, 2),
        "weighted_with_transport": round(weighted_with, 2),
    }


# ── 差旅油資計算（與薪資同步）─────────────────────────────────────────────

def calc_travel_allowance(total_km: float) -> int:
    """依里程級距計算差旅油資：ROUND(km * IF(km<=100, 1.5, IF(km<=200, 2, 3)), 0)"""
    if total_km <= 100:
        return round(total_km * 1.5)
    elif total_km <= 200:
        return round(total_km * 2)
    else:
        return round(total_km * 3)


# ── 月薪資結算 ────────────────────────────────────────────────────────────

def calculate_salary(weighted_minutes: float, hourly_wage: int) -> int:
    hours = round(weighted_minutes / 60, 2)
    return math.ceil(hours * hourly_wage)


def calculate_long_term_bonus(
    caregiver_name_norm: str, hire_date: date, year: int, month: int, hours: int
) -> int:
    name = unicodedata.normalize("NFKC", caregiver_name_norm).strip()
    ref_date = date(year, month, 1)
    years = (ref_date - hire_date).days // 365 if hire_date else 0

    if name in {unicodedata.normalize("NFKC", n).strip() for n in NEW_EMPLOYEES}:
        category = "新人"
    elif name in {unicodedata.normalize("NFKC", n).strip() for n in OLD_EMPLOYEES}:
        category = "舊人"
    else:
        return 0

    if years >= 5:
        level = f"{category}_滿5年"
    elif years >= 3:
        level = f"{category}_滿3年"
    elif years >= 2:
        level = f"{category}_滿2年"
    elif years >= 1:
        level = f"{category}_滿年"
    else:
        return 0

    table = LONG_TERM_BONUS_TABLE.get(level, [])
    for low, high, bonus in table:
        if low <= hours <= high:
            return bonus
    return 0


def calculate_monthly_salary(
    caregiver_id: str, year: int, month: int, db: Session, calculated_by: str = None
) -> MonthlySalary:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    range_start = month_start - timedelta(days=7)
    range_end = month_end + timedelta(days=7)

    caregiver = db.query(User).filter(User.id == caregiver_id).first()
    if not caregiver:
        raise ValueError(f"找不到居服員 {caregiver_id}")

    holiday_dates = set(get_holiday_dates_for_year(db, year).keys())
    svc_records = _get_service_records_by_date(caregiver_id, range_start, range_end, db)

    total_weighted_no = 0.0
    total_weighted_with = 0.0
    total_transfer_mins = 0.0
    total_transfer_km = 0.0
    total_svc_minutes = 0

    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        daily = get_daily_bracket_breakdown(
            caregiver_id, d, db, svc_records, holiday_dates
        )
        if daily:
            total_weighted_no += daily["weighted_no_transport"]
            total_weighted_with += daily["weighted_with_transport"]
            total_transfer_mins += daily["transfer_minutes"]
            total_transfer_km += daily["transfer_km"]
            total_svc_minutes += daily["total_minutes"]

    raw_wage = caregiver.hourly_wage
    hourly_wage = int(raw_wage) if raw_wage is not None else 230
    salary_no = calculate_salary(total_weighted_no, hourly_wage)
    salary_with = calculate_salary(total_weighted_with, hourly_wage)
    allowance = salary_with - salary_no

    travel_allowance = calc_travel_allowance(total_transfer_km)

    total_hours_ceil = math.ceil(total_svc_minutes / 60)
    bonus = 0
    if caregiver.hire_date:
        bonus = calculate_long_term_bonus(
            caregiver.display_name, caregiver.hire_date, year, month, total_hours_ceil
        )

    # 計算保險應扣項目
    from app.models.nhi_dependent import NhiDependent
    from app.services.insurance import calc_health_insurance_subsidy
    active_dep_objs = db.query(NhiDependent).filter(
        NhiDependent.employee_id == caregiver_id,
        NhiDependent.is_active.is_(True),
    ).all()
    active_deps = len(active_dep_objs)

    li_deduction = calc_labor_insurance_self_pay(caregiver.insurance_labor_amount or 0)
    hi_base = calc_health_insurance_self_pay(
        caregiver.insurance_health_amount or 0,
        active_deps,
    )
    hi_subsidy = calc_health_insurance_subsidy(
        caregiver.insurance_health_amount or 0,
        active_dep_objs,
    )
    hi_deduction = hi_base - hi_subsidy
    lp_deduction = calc_labor_pension_self_pay(
        caregiver.insurance_labor_pension_amount or 0,
        caregiver.labor_pension_personal_rate or 0,
    )

    existing = db.query(MonthlySalary).filter(
        MonthlySalary.caregiver_id == caregiver_id,
        MonthlySalary.year == year,
        MonthlySalary.month == month,
    ).first()

    if existing:
        existing.weighted_minutes_no_transport = round(total_weighted_no, 2)
        existing.salary_no_transport = salary_no
        existing.weighted_minutes_with_transport = round(total_weighted_with, 2)
        existing.salary_with_transport = salary_with
        existing.transport_allowance = allowance
        existing.total_transfer_km = round(total_transfer_km, 2)
        existing.total_transfer_minutes = round(total_transfer_mins, 2)
        existing.total_service_minutes = total_svc_minutes
        existing.travel_allowance = travel_allowance
        existing.long_term_bonus = bonus
        existing.labor_insurance_deduction = li_deduction
        existing.health_insurance_deduction = hi_deduction
        existing.labor_pension_deduction = lp_deduction
        existing.calculated_at = datetime.utcnow()
        if calculated_by:
            existing.calculated_by = calculated_by
        ms = existing
    else:
        ms = MonthlySalary(
            caregiver_id=caregiver_id,
            year=year, month=month,
            weighted_minutes_no_transport=round(total_weighted_no, 2),
            salary_no_transport=salary_no,
            weighted_minutes_with_transport=round(total_weighted_with, 2),
            salary_with_transport=salary_with,
            transport_allowance=allowance,
            total_transfer_km=round(total_transfer_km, 2),
            total_transfer_minutes=round(total_transfer_mins, 2),
            total_service_minutes=total_svc_minutes,
            travel_allowance=travel_allowance,
            long_term_bonus=bonus,
            labor_insurance_deduction=li_deduction,
            health_insurance_deduction=hi_deduction,
            labor_pension_deduction=lp_deduction,
            calculated_by=calculated_by,
        )
        db.add(ms)

    db.commit()
    return ms


def calculate_all_monthly_salaries(year: int, month: int, db: Session, calculated_by: str = None) -> list[MonthlySalary]:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    caregiver_ids = (
        db.query(CaregiverServiceRecord.caregiver_id)
        .filter(
            CaregiverServiceRecord.service_date >= month_start,
            CaregiverServiceRecord.service_date <= month_end,
        )
        .distinct()
        .all()
    )

    results = []
    for (cg_id,) in caregiver_ids:
        ms = calculate_monthly_salary(cg_id, year, month, db, calculated_by)
        results.append(ms)
    return results



