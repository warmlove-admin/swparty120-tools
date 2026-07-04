"""
出勤屬性分類邏輯（定版備忘錄 · 2026-07-02）
============================================

【基本原則】
- 一週以星期日為第 1 天，星期六為第 7 天
- 優先順序：國定假日 > 各項週末判斷 > 平日

────────────────────────────────────────────

【① 國定假日（最高優先）】
- 當天存在 NationalHoliday 資料表 → 直接為「國定假日」
- 國定假日遇六日之補假日 → 補假日也視為國定假日
- 國定假日該週六日：仍依下方【④】邏輯判斷（有出勤=休息日，另一天=例假日），
  但該日加班費率以國定假日倍率計算

────────────────────────────────────────────

【② 兼職人員（出勤天僅勾選六日）】
- 六日出勤 → 一律「一般工時」（非加班時數）
- 若出勤日遇到國定假日 → 優先為「國定假日」

────────────────────────────────────────────

【③ 平日（週一至週五）— 非兼職】
- 若非國定假日/補假日 → 一律「一般出勤日」

────────────────────────────────────────────

【④ 週末（六、日）— 非兼職】

資料來源：實際服務紀錄（CaregiverServiceRecord），
          依次月初定案之當月資料為準。

狀況 A：**六日「皆有」出勤**
  → 觸發彈性工時判斷，找出關聯的 14 天視窗：
     - 該週六往前找到最近的週日 = Day 1
     - 該週日往後找到最近的週六 = Day 14
     - 視窗若跨月，以上個月總時數判斷
     - 視窗若在同月，以當月已排定時數判斷
     - 若視窗涵蓋期間月總時數 ≥ 172 小時 → 適用二週變形工時
       - Day 1（第 1 週日）= 例假日
       - Day 14（第 2 週六）= 例假日
       - 中間的週六 = 休息日
       - 中間的週日 = 休息日
     - 若 < 172 小時 → 出勤屬性仍依上述彈性工時邏輯，
       但標記「不符合規範」⚠️

狀況 B：**六日僅一天出勤**
  → 有出勤那天 = 休息日
  → 另一天 = 例假日

狀況 C：**六日皆未出勤**
  → 週六 = 休息日
  → 週日 = 例假日

【14 天關聯視窗範例】
假設 7/4(六)+7/5(日) 都有出勤：
  往前找最近週日 → 6/28(日) = Day 1（例假日）
  往後找最近週六 → 7/11(六) = Day 14（例假日）
  視窗：6/28 ~ 7/11，中間：
    7/4(六) = 休息日
    7/5(日) = 休息日
  若 7/5~7/11 這週的六日也皆有出勤：
    7/12(日) = 下個 14 天週期的 Day 1（例假日），
    不會與上個週期重疊。

【週期不可重疊原則】
- 第 14 天的次日（週日）自動為下個週期的第 1 天
- 不會有同一天同時屬於兩個週期的情況

────────────────────────────────────────────

【⑤ 與 User 欄位的關係】
- regular_off_weekday / rest_weekday 保留為「個人預設值」
- 未來只在「無實際班表資料可判斷時」作為 fallback
- 有實際班表時，一律以④的邏輯動態計算
"""

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.national_holiday import NationalHoliday
from app.models.user import User

WEEKDAY_LABELS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
SUN_FIRST_LABELS = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"]

DATE_TYPE_LABELS = {
    "weekday": "平日",
    "regular_off": "例假日",
    "rest_day": "休息日",
    "national_holiday": "國定假日",
}

DATE_TYPE_COLORS = {
    "weekday": "#e8f4f8",
    "regular_off": "#fce4ec",
    "rest_day": "#fff3e0",
    "national_holiday": "#a5d6a7",
}

OVERTIME_MULTIPLIERS = {
    "weekday": {
        "0-8": 1.00,
        "9-10": 1.34,
        "11-12": 1.67,
    },
    "national_holiday": {
        "0-8": 2.00,
        "9-10": 1.34,
        "11-12": 1.67,
    },
    "regular_off": {
        "0-2": 1.34,
        "3-8": 1.67,
        "9-10": 2.67,
        "11-12": 2.67,
    },
    "rest_day": {
        "0-2": 1.34,
        "3-8": 1.67,
        "9-10": 2.67,
        "11-12": 2.67,
    },
}

BRACKET_CAPS_MINUTES = {
    "weekday": {"0-8": 480, "9-10": 120, "11-12": 120},
    "national_holiday": {"0-8": 480, "9-10": 120, "11-12": 120},
    "regular_off": {"0-2": 120, "3-8": 360, "9-10": 120, "11-12": 120},
    "rest_day": {"0-2": 120, "3-8": 360, "9-10": 120, "11-12": 120},
}

SEED_HOLIDAYS = [
    (date(2026, 1, 1), "元旦"),
    (date(2026, 2, 28), "和平紀念日"),
    (date(2026, 4, 4), "兒童節"),
    (date(2026, 4, 5), "清明節"),
    (date(2026, 5, 1), "勞動節"),
    (date(2026, 6, 19), "端午節"),
    (date(2026, 9, 25), "中秋節"),
    (date(2026, 10, 10), "國慶日"),
    (date(2027, 1, 1), "元旦"),
    (date(2027, 2, 28), "和平紀念日"),
    (date(2027, 4, 4), "兒童節"),
    (date(2027, 4, 5), "清明節"),
    (date(2027, 5, 1), "勞動節"),
    (date(2027, 6, 9), "端午節"),
    (date(2027, 9, 15), "中秋節"),
    (date(2027, 10, 10), "國慶日"),
]


def _classify_fallback(user: User, d: date, holiday_dates: set) -> str:
    """Fallback when no service records: use regular_off_weekday/rest_weekday."""
    if d in holiday_dates:
        return "national_holiday"
    weekday = d.weekday()
    if user.regular_off_weekday is not None and weekday == user.regular_off_weekday:
        return "regular_off"
    if user.rest_weekday is not None and weekday == user.rest_weekday:
        return "rest_day"
    return "weekday"


def _find_week_sun_sat(d: date) -> tuple[date, date]:
    """Return (Sunday, Saturday) of the week containing d (Sun=day 1, Sat=day 7)."""
    days_since_sunday = (d.weekday() + 1) % 7
    sunday = d - timedelta(days=days_since_sunday)
    saturday = sunday + timedelta(days=6)
    return sunday, saturday


def _get_14_day_window(saturday: date, sunday: date) -> tuple[date, date]:
    """Given a Saturday and Sunday that form a weekend, return (day1, day14).

    Day 1 = Sunday before that Saturday's week (例假日)
    Day 14 = Saturday after that Sunday's week (例假日)
    """
    day1 = saturday - timedelta(days=6)
    day14 = sunday + timedelta(days=6)
    return day1, day14


def _has_service_on(svc_by_date: dict, d: date) -> bool:
    return d in svc_by_date and len(svc_by_date[d]) > 0


def _get_month_hours(caregiver_id: str, year: int, month: int, db: Session) -> float:
    """Sum total service hours for a caregiver in a given month."""
    from app.models.caregiver_service_record import CaregiverServiceRecord
    from sqlalchemy import func as sa_func
    total = (
        db.query(sa_func.coalesce(sa_func.sum(CaregiverServiceRecord.minutes), 0))
        .filter(
            CaregiverServiceRecord.caregiver_id == caregiver_id,
            sa_func.extract("year", CaregiverServiceRecord.service_date) == year,
            sa_func.extract("month", CaregiverServiceRecord.service_date) == month,
        )
        .scalar()
    )
    return total / 60.0


def classify_date_from_records(
    user: User,
    d: date,
    holiday_dates: set,
    service_records_by_date: dict,
    db: Session,
) -> tuple[str, bool]:
    """Determine date type and whether it's non-compliant.

    Returns (date_type, is_non_compliant).

    Priority: national_holiday > part-time > weekday > weekend rules.
    """
    # ── ① 國定假日（最高優先） ──
    if d in holiday_dates:
        return "national_holiday", False

    # ── ② 兼職人員（出勤天僅勾六日） ──
    if user.is_part_time:
        # 六日出勤 = 一般工時（非加班）
        if d.weekday() in (5, 6) and _has_service_on(service_records_by_date, d):
            return "weekday", False
        return _classify_fallback(user, d, holiday_dates), False

    # ── ③ 平日（週一至週五） ──
    if d.weekday() < 5:  # Mon=0 ... Fri=4
        return "weekday", False

    # ── ④ 週末（六、日） ──
    # 找出同一個週末的六日配對（六日可能跨 Sun-Week）
    if d.weekday() == 5:  # Saturday
        paired_sunday = d + timedelta(days=1)
        has_sat_svc = _has_service_on(service_records_by_date, d)
        has_sun_svc = _has_service_on(service_records_by_date, paired_sunday)
        weekend_sat, weekend_sun = d, paired_sunday
    else:  # Sunday (weekday() == 6)
        paired_saturday = d - timedelta(days=1)
        has_sat_svc = _has_service_on(service_records_by_date, paired_saturday)
        has_sun_svc = _has_service_on(service_records_by_date, d)
        weekend_sat, weekend_sun = paired_saturday, d

    # 狀況 A：六日「皆有」出勤 → 14 天關聯視窗
    if has_sat_svc and has_sun_svc:
        day1, day14 = _get_14_day_window(weekend_sat, weekend_sun)

        # 判斷視窗是否跨月，選用正確月份計算 172h
        if day1.month != day14.month:
            prev = weekend_sun.replace(day=1) - timedelta(days=1)
            monthly_hours = _get_month_hours(user.id, prev.year, prev.month, db)
        else:
            monthly_hours = _get_month_hours(user.id, d.year, d.month, db)

        is_non_compliant = monthly_hours < 172.0

        if d == day1 or d == day14:
            return "regular_off", is_non_compliant
        return "rest_day", is_non_compliant

    # 狀況 B：僅一天出勤
    if d.weekday() == 5:  # Saturday
        if has_sat_svc:
            return "rest_day", False
        return "regular_off", False
    else:  # Sunday
        if has_sun_svc:
            return "rest_day", False
        return "regular_off", False

    # 狀況 C：皆未出勤（上面已涵蓋：週六→休息日、週日→例假日）


def classify_date(user: User, d: date, db: Session) -> str:
    """Backward-compatible wrapper: queries service records and holidays."""
    holiday_dates = set(get_holiday_dates_for_year(db, d.year).keys())
    records = _get_service_records_in_range(user.id, d, d, db)
    svc_by_date = {k: v for k, v in records.items() if v}
    date_type, _ = classify_date_from_records(user, d, holiday_dates, svc_by_date, db)
    return date_type


def classify_date_no_db(user: User, d: date, holiday_dates: set) -> str:
    """Simplified version for contexts without DB access (falls back to old logic)."""
    return _classify_fallback(user, d, holiday_dates)


def _get_service_records_in_range(
    caregiver_id: str, start: date, end: date, db: Session
) -> dict[date, list]:
    """Fetch service records for a caregiver in date range, grouped by date."""
    from app.models.caregiver_service_record import CaregiverServiceRecord
    records = (
        db.query(CaregiverServiceRecord)
        .filter(
            CaregiverServiceRecord.caregiver_id == caregiver_id,
            CaregiverServiceRecord.service_date >= start,
            CaregiverServiceRecord.service_date <= end,
        )
        .all()
    )
    result: dict[date, list] = {}
    for r in records:
        result.setdefault(r.service_date, []).append(r)
    return result


def get_holiday_dates_for_year(db: Session, year: int) -> dict:
    holidays = db.query(NationalHoliday).filter(NationalHoliday.year == year).all()
    return {h.holiday_date: h.name for h in holidays}


def get_multiplier(date_type: str, bracket: str) -> float:
    brackets = OVERTIME_MULTIPLIERS.get(date_type, {})
    return brackets.get(bracket, 1.0)


def seed_national_holidays(db: Session) -> int:
    count = 0
    for d, name in SEED_HOLIDAYS:
        exists = db.query(NationalHoliday).filter(NationalHoliday.holiday_date == d).first()
        if not exists:
            db.add(NationalHoliday(holiday_date=d, name=name, year=d.year))
            count += 1
    if count:
        db.commit()
    return count


def month_calendar_data(
    user: User, year: int, month: int, db: Session
) -> list[dict]:
    import calendar
    _, days_in_month = calendar.monthrange(year, month)
    holiday_map = get_holiday_dates_for_year(db, year)
    holiday_dates = set(holiday_map.keys())

    # Fetch service records for the whole month (plus 1 week buffer for 14-day window)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    range_start = month_start - timedelta(days=7)
    range_end = month_end + timedelta(days=7)
    svc_records = _get_service_records_in_range(user.id, range_start, range_end, db)

    result = []
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        date_type, is_non_compliant = classify_date_from_records(
            user, d, holiday_dates, svc_records, db
        )
        holiday_name = holiday_map.get(d)
        item = {
            "date": d,
            "day": day_num,
            "weekday": d.weekday(),
            "weekday_label": WEEKDAY_LABELS[d.weekday()],
            "date_type": date_type,
            "date_type_label": DATE_TYPE_LABELS[date_type],
            "color": DATE_TYPE_COLORS[date_type],
            "holiday_name": holiday_name,
        }
        if is_non_compliant:
            item["warning"] = "⚠️ 彈性工時時數未達172h"
        result.append(item)
    return result


def all_caregivers_calendar(
    year: int, month: int, db: Session
) -> dict:
    import calendar
    from app.models.user import UserRole
    _, days_in_month = calendar.monthrange(year, month)
    holiday_map = get_holiday_dates_for_year(db, year)
    holiday_dates = set(holiday_map.keys())
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    range_start = month_start - timedelta(days=7)
    range_end = month_end + timedelta(days=7)

    caregivers = (
        db.query(User)
        .filter(User.role == UserRole.caregiver, User.is_active.is_(True))
        .order_by(User.display_name)
        .all()
    )
    result = {}
    for cg in caregivers:
        svc_records = _get_service_records_in_range(cg.id, range_start, range_end, db)
        days = []
        for day_num in range(1, days_in_month + 1):
            d = date(year, month, day_num)
            date_type, is_non_compliant = classify_date_from_records(
                cg, d, holiday_dates, svc_records, db
            )
            item = {
                "day": day_num,
                "date_type": date_type,
                "date_type_label": DATE_TYPE_LABELS[date_type],
                "holiday_name": holiday_map.get(d),
            }
            if is_non_compliant:
                item["warning"] = "⚠️"
            days.append(item)
        result[cg] = days
    return result, days_in_month
