"""AA 碼清冊匯入與獎金計算

AA01/AA02/AA08/AA09 → 不發給居服員
其餘 AA 碼：政府價格對半拆（公司 50%、居服員 50%）
多人共同服務 → 居服員均分那 50%
AA05：BA16-only 的居服員不發給
AA06：需對照勾稽條件 + 班表比對誰符合
AA07：多人共班時 380 按月依服務天數比例拆分
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

import openpyxl
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.aa_code import AaCodeRecord, Aa06CaseCondition
from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.case import Case
from app.models.monthly_salary import MonthlySalary
from app.models.user import User

# AA 碼不予發放給居服員的清單
EXCLUDED_AA_CODES = {"AA01", "AA02", "AA08", "AA09"}

# AA06 各條件對應的 BA 服務代碼
AA06_CONDITION_BA = {
    1: {"BA01", "BA07"},
    2: {"BA12"},
    3: {"BA12"},
    4: {"BA01", "BA02", "BA07"},
}

AA07_MONTHLY_AMOUNT = 380  # AA07 每月固定總額，多人共班按天數比例拆分


def parse_aa_excel(filepath: str) -> list[dict]:
    """解析 AA 碼清冊 Excel，回傳 list of dict"""
    wb = openpyxl.load_workbook(filepath)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(min_row=5, values_only=True):
        aa_code = row[1]
        if not aa_code or not str(aa_code).strip():
            continue
        case_idno = str(row[5] or "").strip()
        case_name = str(row[6] or "").strip()
        price_raw = str(row[7] or "0").strip()
        qty = row[8] or 0
        service_dates_raw = str(row[10] or "").strip()
        service_personnel = str(row[16] or "").strip()
        remark = str(row[17] or "").strip() if len(row) > 17 else ""

        # 解析價格 A/B → 取 A（機構價格）
        price_a = _parse_price_a(price_raw)

        # 解析服務日期
        dates = _parse_dates(service_dates_raw)

        # 解析服務人員
        personnel = _parse_personnel(service_personnel)

        rows.append({
            "aa_code": str(aa_code).strip(),
            "case_idno": case_idno,
            "case_name": case_name,
            "price_a": price_a,
            "qty": qty,
            "dates": dates,
            "personnel": personnel,
            "remark": remark,
        })
    return rows


def _parse_price_a(price_raw: str) -> int:
    """從 '200/240' 取出 200"""
    m = re.match(r"(\d+)", price_raw)
    return int(m.group(1)) if m else 0


def _parse_dates(raw: str) -> list[date]:
    """解析 '115/05/01,115/05/04,...' → [date, ...]"""
    if not raw:
        return []
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            parts = part.split("/")
            if len(parts) == 3:
                y = int(parts[0]) + 1911
                m = int(parts[1])
                d = int(parts[2])
                result.append(date(y, m, d))
        except (ValueError, IndexError):
            pass
    return result


def _parse_personnel(raw: str) -> list[str]:
    """解析 '張峯銘、陳偉盛' → ['張峯銘', '陳偉盛']"""
    if not raw:
        return []
    return [p.strip() for p in raw.replace("、", ",").split(",") if p.strip()]


def _get_or_create_case(db: Session, idno: str, name: str) -> Optional[Case]:
    """依身分證號找個案，找不到則略過"""
    case = db.query(Case).filter(Case.id_number == idno).first()
    return case


def _get_caregiver_by_name(db: Session, name: str) -> Optional[User]:
    """依顯示名稱找居服員"""
    return db.query(User).filter(
        User.display_name == name,
        User.role == "居服員",
    ).first()


def _caregiver_ba16_only_on_date(
    db: Session, caregiver_id: str, case_id: str, svc_date: date
) -> bool:
    """檢查該居服員當天對該個案是否只提供 BA16"""
    records = db.query(CaregiverServiceRecord).filter(
        CaregiverServiceRecord.caregiver_id == caregiver_id,
        CaregiverServiceRecord.case_id == case_id,
        CaregiverServiceRecord.service_date == svc_date,
    ).all()
    all_codes = set()
    for r in records:
        if r.service_codes:
            for part in r.service_codes.split(","):
                code = part.strip().split("x")[0].strip()
                if code:
                    all_codes.add(code)
    if not all_codes:
        return False
    return all_codes == {"BA16"}


def _get_aa06_conditions(db: Session, case_id: str) -> Optional[list[int]]:
    """取得該個案已設定的 AA06 條件"""
    cond = db.query(Aa06CaseCondition).filter(Aa06CaseCondition.case_id == case_id).first()
    if cond:
        return [int(x) for x in cond.conditions.split(",") if x.strip()]
    return None


def _match_aa06_condition(
    db: Session, case_id: str, svc_date: date, caregiver_names: list[str],
) -> list[str]:
    """比對 AA06 條件：回傳符合條件的居服員名稱列表"""
    conditions = _get_aa06_conditions(db, case_id)
    if not conditions:
        return caregiver_names  # 尚未設定條件 → 暫不分（由後續 UI 處理）

    qualifying = []
    for cg_name in caregiver_names:
        cg = _get_caregiver_by_name(db, cg_name)
        if not cg:
            qualifying.append(cg_name)
            continue
        for cond_num in conditions:
            ba_codes = AA06_CONDITION_BA.get(cond_num, set())
            if not ba_codes:
                continue
            records = db.query(CaregiverServiceRecord).filter(
                CaregiverServiceRecord.caregiver_id == cg.id,
                CaregiverServiceRecord.case_id == case_id,
                CaregiverServiceRecord.service_date == svc_date,
            ).all()
            found = False
            for r in records:
                if r.service_codes:
                    for part in r.service_codes.split(","):
                        code = part.strip().split("x")[0].strip()
                        if code in ba_codes:
                            qualifying.append(cg_name)
                            found = True
                            break
                if found:
                    break
            if found:
                break
    return qualifying if qualifying else caregiver_names


def import_aa_file(
    db: Session,
    filepath: str,
    source_label: str = "",
    source_type: str = "",
    target_year: int | None = None,
    target_month: int | None = None,
) -> dict:
    """匯入 AA 清冊並計算獎金分配

    target_year/target_month：指定 year/month 歸屬月份（即發放/處理月份）。
    若未指定則沿用服務日期（svc_date）的月份。
    """
    rows = parse_aa_excel(filepath)
    stats = {"total": len(rows), "skipped": 0, "allocated": 0, "errors": []}
    allocations = []  # list of dict for AaCodeRecord

    # AA07 逐月收集器：{(case_id, y, m): {svc_date: {cg_name, ...}}}
    aa07_collect: dict[tuple, dict[date, set[str]]] = defaultdict(lambda: defaultdict(set))
    aa07_cg_name_to_id: dict[str, str] = {}  # 快取居服員名稱→id

    for row in rows:
        aa_code = row["aa_code"]
        if aa_code in EXCLUDED_AA_CODES:
            stats["skipped"] += 1
            continue

        case = _get_or_create_case(db, row["case_idno"], row["case_name"])
        if not case:
            stats["errors"].append(f"找不到個案 {row['case_name']}({row['case_idno']})")
            continue

        price_a = row["price_a"]
        if price_a <= 0:
            continue
        cg_share_total = price_a // 2  # 居服員總額 = 政府價格的一半

        for svc_date in row["dates"]:
            personnel = row["personnel"]
            if not personnel:
                continue

            year = target_year if target_year is not None else svc_date.year
            month = target_month if target_month is not None else svc_date.month

            # AA05：排除 BA16-only 的居服員
            if aa_code == "AA05":
                qualified = []
                for name in personnel:
                    cg = _get_caregiver_by_name(db, name)
                    if cg and _caregiver_ba16_only_on_date(db, cg.id, case.id, svc_date):
                        continue  # BA16-only 不發給
                    qualified.append(name)
                if not qualified:
                    continue  # 所有人都 BA16-only
                personnel = qualified

            # AA06：條件過濾
            if aa_code == "AA06":
                conditions = _get_aa06_conditions(db, case.id)
                if not conditions:
                    # 尚未設定條件 → 暫不給獎金，但回報待設定
                    pending_key = (case.id, case.name if case.name else "")
                    stats.setdefault("pending_aa06", {}).setdefault(pending_key, set()).update(personnel)
                    # 仍寫入 AaCodeRecord（caregiver_share=0），讓頁面能找到這些個案
                    for name in personnel:
                        cg = _get_caregiver_by_name(db, name)
                        if cg:
                            allocations.append({
                                "caregiver_id": cg.id,
                                "case_id": case.id,
                                "aa_code": aa_code,
                                "service_date": svc_date,
                                "unit_price": price_a,
                                "caregiver_share": 0,
                                "year": year,
                                "month": month,
                                "source_file": f"[{source_type}] {source_label}" if source_type else source_label,
                            })
                    continue
                personnel = _match_aa06_condition(db, case.id, svc_date, personnel)

            # AA07：收集按月資料，後續依服務天數比例拆分
            # 注意：key 用 svc_date 的「服務月份」而非 target_month（處理月份），
            # 這樣查班表時才能找到當月實際服務紀錄。
            if aa_code == "AA07":
                svc_key = (case.id, svc_date.year, svc_date.month)
                for name in personnel:
                    aa07_collect[svc_key][svc_date].add(name)
                    if name not in aa07_cg_name_to_id:
                        cg = _get_caregiver_by_name(db, name)
                        if cg:
                            aa07_cg_name_to_id[name] = cg.id
                continue  # 暫不加入 allocation，主迴圈結束後統一處理

            # 均分
            n = len(personnel)
            if n == 0:
                continue
            per_person = cg_share_total // n
            remainder = cg_share_total % n

            for i, name in enumerate(personnel):
                cg = _get_caregiver_by_name(db, name)
                if not cg:
                    stats["errors"].append(f"找不到居服員 {name}")
                    continue
                amount = per_person + (1 if i < remainder else 0)
                allocations.append({
                    "caregiver_id": cg.id,
                    "case_id": case.id,
                    "aa_code": aa_code,
                    "service_date": svc_date,
                    "unit_price": price_a,
                    "caregiver_share": amount,
                    "year": year,
                    "month": month,
                    "source_file": f"[{source_type}] {source_label}" if source_type else source_label,
                })
                stats["allocated"] += 1

    # ── AA07 後處理：按月依服務天數比例拆分 380 ──
    for (case_id, svc_y, svc_m), date_data in aa07_collect.items():
        # 從班表取得實際服務天數（優先）或回退到 Excel 日期
        all_records = db.query(CaregiverServiceRecord).filter(
            CaregiverServiceRecord.case_id == case_id,
            CaregiverServiceRecord.service_date >= date(svc_y, svc_m, 1),
            CaregiverServiceRecord.service_date < date(svc_y + (svc_m // 12), (svc_m % 12) + 1, 1),
        ).all()

        cg_day_count: dict[str, int] = defaultdict(int)
        if all_records:
            unique_dates: set[date] = set()
            cg_dates: dict[str, set[date]] = defaultdict(set)
            for r in all_records:
                cg_dates[r.caregiver_id].add(r.service_date)
                unique_dates.add(r.service_date)
            # 補充：Excel 列出的居服員若不在班表內仍加入（班表可能不完整）
            for svc_date_excel, names in date_data.items():
                for name in names:
                    cg_id = aa07_cg_name_to_id.get(name)
                    if cg_id and cg_id not in cg_dates:
                        cg_dates[cg_id].add(svc_date_excel)
                        unique_dates.add(svc_date_excel)
            total_d = len(unique_dates)
            for cg_id, dates in cg_dates.items():
                cg_day_count[cg_id] = len(dates)
        else:
            # 回退：用 Excel 的日期（每個日期所有居服員都算一天）
            unique_excel_dates: set[date] = set()
            cg_excel_dates: dict[str, set[date]] = defaultdict(set)
            for svc_date, names in date_data.items():
                unique_excel_dates.add(svc_date)
                for name in names:
                    cg_id = aa07_cg_name_to_id.get(name)
                    if cg_id:
                        cg_excel_dates[cg_id].add(svc_date)
            total_d = len(unique_excel_dates)
            for cg_id, dates in cg_excel_dates.items():
                cg_day_count[cg_id] = len(dates)

        if total_d == 0 or not cg_day_count:
            continue

        # 儲存時用 target_month（處理月份），非服務月份
        store_y = target_year if target_year is not None else svc_y
        store_m = target_month if target_month is not None else svc_m

        remaining = AA07_MONTHLY_AMOUNT
        sorted_cgs = sorted(cg_day_count.items(), key=lambda x: -x[1])
        for i, (cg_id, days) in enumerate(sorted_cgs):
            amount = remaining if i == len(sorted_cgs) - 1 else AA07_MONTHLY_AMOUNT * days // total_d
            remaining -= amount
            allocations.append({
                "caregiver_id": cg_id,
                "case_id": case_id,
                "aa_code": "AA07",
                "service_date": date(svc_y, svc_m, 1),
                "unit_price": AA07_MONTHLY_AMOUNT,
                "caregiver_share": amount,
                "year": store_y,
                "month": store_m,
                "source_file": f"[{source_type}] {source_label}" if source_type else source_label,
            })
            stats["allocated"] += 1

    return {"rows": rows, "allocations": allocations, "stats": stats}


def _calculate_aa_bonus_totals(db: Session, year: int, month: int) -> dict[tuple, int]:
    """從 AaCodeRecord 重新加總兩種來源的 caregiver_share，回傳 {(cg_id, y, m): total}"""
    records = db.query(AaCodeRecord).filter(
        AaCodeRecord.year == year,
        AaCodeRecord.month == month,
        AaCodeRecord.caregiver_share > 0,
    ).all()
    totals: dict[tuple, int] = defaultdict(int)
    for r in records:
        totals[(r.caregiver_id, r.year, r.month)] += r.caregiver_share
    return dict(totals)


def _write_aa_bonus(db: Session, cg_totals: dict[tuple, int], year: int, month: int):
    """將 cg_totals 寫入 MonthlySalary（該月 aa_bonus 欄位）"""
    db.query(MonthlySalary).filter(
        MonthlySalary.year == year,
        MonthlySalary.month == month,
    ).update({"aa_bonus": 0}, synchronize_session=False)
    db.flush()
    for (cg_id, y, m), total in cg_totals.items():
        ms = db.query(MonthlySalary).filter(
            MonthlySalary.caregiver_id == cg_id,
            MonthlySalary.year == y,
            MonthlySalary.month == m,
        ).first()
        if ms:
            ms.aa_bonus += total
        else:
            db.add(MonthlySalary(
                caregiver_id=cg_id, year=y, month=m,
                aa_bonus=total, calculated_at=datetime.utcnow(),
            ))


def save_allocations(db: Session, allocations: list[dict], year: int, month: int, source_type: str = "") -> dict:
    """儲存 AA 碼分配結果到資料庫，並更新 MonthlySalary.aa_bonus

    兩種檔案類型（居家服務+喘息、居家短照）各自獨立管理：
    - 只刪除同一類型的舊 AaCodeRecord（兩者可共存）
    - aa_bonus 從所有類型的 AaCodeRecord 重新加總，確保合併正確
    """
    # 只刪除同一檔案類型的舊記錄（兩種檔案類型可共存）
    q = db.query(AaCodeRecord).filter(
        AaCodeRecord.year == year,
        AaCodeRecord.month == month,
    )
    if source_type:
        q = q.filter(AaCodeRecord.source_file.startswith(f"[{source_type}]"))
    q.delete(synchronize_session=False)

    for alloc in allocations:
        db.add(AaCodeRecord(**alloc))
    db.flush()

    # 從所有來源重新加總，確保兩種檔案合併正確
    cg_totals = _calculate_aa_bonus_totals(db, year, month)
    _write_aa_bonus(db, cg_totals, year, month)

    # ── 帶入次月薪資：AA 獎金比薪資晚一個月 ──
    # 匯入 5 月 AA → 同時更新 6 月 MonthlySalary.aa_bonus（值與 5 月相同）
    next_y = year if month < 12 else year + 1
    next_m = month + 1 if month < 12 else 1
    _write_aa_bonus(db, cg_totals, next_y, next_m)

    db.commit()
    return {"total_cg": len(cg_totals), "total_records": len(allocations)}
