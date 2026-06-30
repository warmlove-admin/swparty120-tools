r"""Import case roster data from the agency case-count XLS file.

Usage:
  .\.venv\Scripts\python.exe -c "import sys; from scripts.import_case_summary_xls import main; sys.exit(main())" -- --file C:\Users\USER\Downloads\案量統計20260627.xls --dry-run
  .\.venv\Scripts\python.exe -c "import sys; from scripts.import_case_summary_xls import main; sys.exit(main())" -- --file C:\Users\USER\Downloads\案量統計20260627.xls
"""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import xlrd

from app.auth import hash_password
from app.database import Base, SessionLocal, apply_compatible_schema_updates, engine
from app.models.case import Case, CaseStatus, CloseReasonType, PauseReasonType
from app.models.contact import Contact, ContactRole
from app.models.user import User, UserRole


SHEET_NAME = "案量明細"
DEFAULT_PASSWORD_PREFIX = "imported-supervisor-"
COLUMNS = {
    "org_case_no": 1,
    "received_date": 2,
    "open_date": 3,
    "case_name": 4,
    "gender": 5,
    "id_number": 6,
    "birth_date": 7,
    "address": 8,
    "living_status": 10,
    "case_phone_day": 11,
    "case_phone_night": 12,
    "case_mobile": 13,
    "welfare_status": 14,
    "cms_level": 15,
    "disability_category": 19,
    "supervisor": 20,
    "contact_name": 21,
    "contact_relation": 22,
    "contact_phone_day": 23,
    "contact_phone_night": 24,
    "contact_mobile": 25,
    "contact_address": 26,
    "a_unit_name": 27,
    "service_status": 29,
    "pause_date": 30,
    "pause_reason": 31,
    "pause_note": 32,
    "close_date": 33,
    "close_reason": 34,
    "close_note": 35,
}
DATE_KEYS = {"received_date", "open_date", "birth_date", "pause_date", "close_date"}


def _clean(value) -> str:
    text = "" if value is None else str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return "" if text.lower() == "null" else text


def _date(value, datemode: int) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, float) or isinstance(value, int):
        try:
            return xlrd.xldate.xldate_as_datetime(value, datemode).date()
        except Exception:
            return None
    text = _clean(value)
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def _phone(*values) -> str | None:
    parts = [_clean(value) for value in values]
    return " / ".join(part for part in parts if part) or None


def _residence_district(address: str | None) -> str | None:
    if not address:
        return None
    match = re.search(r"([^縣市]{1,8}[區鄉鎮市])", address)
    return match.group(1) if match else None


def _status(row: dict) -> CaseStatus:
    status_text = row["service_status"]
    if "結" in status_text or row["close_date"]:
        return CaseStatus.closed
    if any(keyword in status_text for keyword in ("服務中", "正常", "使用中", "開案")):
        return CaseStatus.active
    if "停" in status_text or row["pause_date"]:
        return CaseStatus.paused
    return CaseStatus.active


def _supervisor_username(db, display_name: str) -> str:
    base = f"sup{db.query(User).filter(User.role == UserRole.supervisor).count() + 1:03d}"
    username = base
    index = 1
    while db.query(User).filter(User.username == username).first():
        index += 1
        username = f"{base}_{index}"
    return username


def _get_or_create_supervisor(db, display_name: str) -> User | None:
    if not display_name:
        return None
    user = (
        db.query(User)
        .filter(User.display_name == display_name, User.role.in_([UserRole.supervisor, UserRole.manager]))
        .first()
    )
    if user:
        return user
    user = User(
        username=_supervisor_username(db, display_name),
        display_name=display_name,
        password_hash=hash_password(DEFAULT_PASSWORD_PREFIX + uuid4().hex),
        role=UserRole.supervisor,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _read_rows(path: Path) -> list[dict]:
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_name(SHEET_NAME)
    rows = []
    for row_index in range(1, sheet.nrows):
        row = {"_row_index": row_index}
        for key, col in COLUMNS.items():
            cell = sheet.cell_value(row_index, col)
            if key in DATE_KEYS:
                row[key] = _date(cell, book.datemode)
            else:
                row[key] = _clean(cell)
        if not row.get("org_case_no") and row.get("case_name"):
            row["org_case_no"] = f"XLSROW{row_index:04d}"
        if row.get("org_case_no") and row.get("case_name"):
            rows.append(row)
    return rows


def _upsert_contact(db, case: Case, row: dict) -> bool:
    name = row["contact_name"]
    if not name:
        return False
    contact = (
        db.query(Contact)
        .filter(Contact.case_id == case.id, Contact.contact_role == ContactRole.primary_contact)
        .first()
    )
    if not contact:
        contact = Contact(case_id=case.id, contact_role=ContactRole.primary_contact, name=name)
        db.add(contact)
    contact.name = name
    contact.relation = row["contact_relation"] or None
    contact.phone = _phone(row["contact_phone_day"], row["contact_phone_night"], row["contact_mobile"])
    contact.note = row["contact_address"] or None
    return True


def import_cases(path: Path, dry_run: bool = False) -> dict:
    rows = _read_rows(path)
    db = SessionLocal()
    stats = {
        "rows": len(rows),
        "created_cases": 0,
        "updated_cases": 0,
        "created_supervisors": 0,
        "upserted_contacts": 0,
        "active": 0,
        "paused": 0,
        "closed": 0,
    }
    try:
        before_supervisors = db.query(User).count()
        for row in rows:
            supervisor = _get_or_create_supervisor(db, row["supervisor"])
            case = db.query(Case).filter(Case.org_case_no == row["org_case_no"]).first()
            if case:
                stats["updated_cases"] += 1
            else:
                case = Case(
                    org_case_no=row["org_case_no"],
                    name=row["case_name"],
                    id_number=row["id_number"] or row["org_case_no"],
                )
                db.add(case)
                stats["created_cases"] += 1

            case.name = row["case_name"]
            case.id_number = row["id_number"] or row["org_case_no"]
            case.birth_date = row["birth_date"]
            case.gender = row["gender"] or None
            case.phone = _phone(row["case_phone_day"], row["case_phone_night"], row["case_mobile"])
            case.household_address = row["address"] or None
            case.residence_address = row["address"] or None
            case.ltc_welfare_status = row["welfare_status"] or None
            case.disability_category = row["disability_category"] or None
            case.cms_level = row["cms_level"] or None
            case.living_status = row["living_status"] or None
            case.residence_district = _residence_district(row["address"])
            case.a_unit_name = row["a_unit_name"] or None
            case.primary_supervisor_id = supervisor.id if supervisor else None
            case.open_date = row["open_date"] or row["received_date"]
            case.status = _status(row)
            case.pause_date = row["pause_date"]
            case.pause_reason_type = PauseReasonType.other if row["pause_reason"] or row["pause_note"] else None
            case.pause_reason_note = _phone(row["pause_reason"], row["pause_note"])
            case.close_date = row["close_date"]
            case.close_reason_type = CloseReasonType.other if row["close_reason"] or row["close_note"] else None
            case.close_reason_note = _phone(row["close_reason"], row["close_note"])

            db.flush()
            if _upsert_contact(db, case, row):
                stats["upserted_contacts"] += 1
            stats[case.status.name] += 1

        stats["created_supervisors"] = db.query(User).count() - before_supervisors
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    Base.metadata.create_all(bind=engine)
    apply_compatible_schema_updates()
    stats = import_cases(args.file, dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "IMPORTED"
    print(mode)
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
