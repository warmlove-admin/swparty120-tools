"""AA 碼清冊匯入與獎金計算

AA01/AA02/AA08/AA09 → 不發給居服員
其餘 AA 碼：政府價格對半拆（公司 50%、居服員 50%）
多人共同服務 → 居服員均分那 50%
AA05：BA16-only 的居服員不發給
AA06：需對照勾稽條件 + 班表比對誰符合
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
                                "source_file": source_label,
                            })
                    continue
                personnel = _match_aa06_condition(db, case.id, svc_date, personnel)

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
                    "source_file": source_label,
                })
                stats["allocated"] += 1

    return {"rows": rows, "allocations": allocations, "stats": stats}


def save_allocations(db: Session, allocations: list[dict], year: int, month: int) -> dict:
    """儲存 AA 碼分配結果到資料庫，並更新 MonthlySalary.aa_bonus"""
    # 先刪除該月份的舊分配記錄
    db.query(AaCodeRecord).filter(
        AaCodeRecord.year == year,
        AaCodeRecord.month == month,
    ).delete(synchronize_session=False)

    # 重置該月份所有 aa_bonus（避免重複累加）
    db.query(MonthlySalary).filter(
        MonthlySalary.year == year,
        MonthlySalary.month == month,
    ).update({"aa_bonus": 0}, synchronize_session=False)
    db.flush()

    for alloc in allocations:
        db.add(AaCodeRecord(**alloc))
    db.flush()

    # 更新 MonthlySalary.aa_bonus（以重置後的 0 為基準累加）
    cg_totals = defaultdict(int)
    for alloc in allocations:
        cg_totals[(alloc["caregiver_id"], alloc["year"], alloc["month"])] += alloc["caregiver_share"]

    for (cg_id, y, m), total in cg_totals.items():
        ms = db.query(MonthlySalary).filter(
            MonthlySalary.caregiver_id == cg_id,
            MonthlySalary.year == y,
            MonthlySalary.month == m,
        ).first()
        if ms:
            ms.aa_bonus += total
        else:
            ms = MonthlySalary(
                caregiver_id=cg_id,
                year=y, month=m,
                aa_bonus=total,
                calculated_at=datetime.utcnow(),
            )
            db.add(ms)

    db.commit()
    return {"total_cg": len(cg_totals), "total_records": len(allocations)}
