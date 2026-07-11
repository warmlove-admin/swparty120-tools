"""從 案量統計20260711.xls 批量更新個案資料（只更新已存在的個案，不新增）"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

from app.database import SessionLocal
from app.models.case import Case


EXCEL_PATH = r"C:\Users\USER\Downloads\案量統計20260711.xls"
SHEET = "案量明細"

# Excel 欄位位置（0-indexed）→ Case model 欄位
COL_MAP = {
    1: "org_case_no",         # 案號（用於比對）
    8: "residence_address",   # 現居地址
    26: "household_address",  # 戶籍地址
    14: "ltc_welfare_status", # 長照福利別
    19: "disability_category",# 身障類別
    15: "cms_level",          # 失能等級
    27: "a_unit_name",        # 社區照顧服務體系
    20: "case_manager_name",  # 居督
    10: "living_status",      # 居住狀況
    13: "phone",              # 手機號碼
}

UPDATE_FIELDS = [
    "residence_address", "household_address", "ltc_welfare_status",
    "disability_category", "cms_level", "a_unit_name", "case_manager_name",
    "living_status", "phone", "residence_district",
]


def normalize_org_no(val) -> str:
    """Excel 案號可能是 float（824.0）或字串，統一轉 4 位零補齊字串"""
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if not s:
        return ""
    try:
        num = int(float(s))
        return f"{num:04d}"
    except ValueError:
        return s


def extract_district(address: str) -> str:
    """從地址提取縣市+行政區，如「新北市汐止區白雲里汐碇路370巷2號」→「新北市汐止區」"""
    if not address:
        return ""
    m = re.match(r"[\u4e00-\u9fff]{1,3}(?:市|縣)[\u4e00-\u9fff]{1,5}(?:區|市)", address)
    return m.group(0) if m else ""


def read_excel() -> list[dict]:
    df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET, header=0, dtype=str)
    records = []
    for _, row in df.iterrows():
        rec = {
            "org_case_no": normalize_org_no(row.iloc[1]) if 1 < len(row) else "",
        }
        if not rec["org_case_no"]:
            continue
        for col_idx, field in COL_MAP.items():
            if col_idx == 1:
                continue
            val = row.iloc[col_idx] if col_idx < len(row) else ""
            rec[field] = str(val).strip() if pd.notna(val) and str(val).strip() else ""
        addr = rec.get("residence_address", "")
        rec["residence_district"] = extract_district(addr)
        records.append(rec)
    return records


def main():
    print(f"讀取 {EXCEL_PATH} ...")
    records = read_excel()
    print(f"Excel 有效個案（有案號）：{len(records)} 筆")

    db = SessionLocal()
    try:
        matched = 0
        updated = 0
        skipped_no_change = 0
        detail = []

        for rec in records:
            org_no = rec["org_case_no"]
            case = db.query(Case).filter(Case.org_case_no == org_no).first()
            if not case:
                continue

            matched += 1
            dirty = False
            changed_fields = []

            for field in UPDATE_FIELDS:
                excel_val = rec.get(field, "")
                db_val = getattr(case, field, None)
                if excel_val and not db_val:
                    setattr(case, field, excel_val)
                    dirty = True
                    changed_fields.append(field)

            # 行政區特例：DB 已有「新北市」但 Excel 可提供完整「新北市汐止區」
            excel_dist = rec.get("residence_district", "")
            if excel_dist and len(excel_dist) > len(case.residence_district or ""):
                case.residence_district = excel_dist
                if "residence_district" not in changed_fields:
                    dirty = True
                    changed_fields.append("residence_district")

            if dirty:
                updated += 1
                detail.append(f"  {org_no} {case.name}: {', '.join(changed_fields)}")
            else:
                skipped_no_change += 1

        db.commit()

        print(f"\n完成！")
        print(f"  DB 總個案數：{db.query(Case).count()}")
        print(f"  Excel 比對到：{matched} 筆")
        print(f"  已更新：{updated} 筆")
        print(f"  無須更新：{skipped_no_change} 筆")
        if detail:
            print(f"\n更新明細：")
            for d in detail:
                print(d)

    finally:
        db.close()


if __name__ == "__main__":
    main()
