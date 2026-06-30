"""Import personnel data from PA_*.xlsx into users."""

from __future__ import annotations

import argparse
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
import xml.etree.ElementTree as ET

from app.auth import hash_password
from app.database import SessionLocal, apply_compatible_schema_updates
from app.models.user import User, UserRole


NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
SHEET_NAME = "在職名單"
DATE_COLUMNS = {"到職日", "離職生效日", "出生年月日"}


def _clean(value) -> str:
    text = "" if value is None else str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return "" if text.lower() == "null" else text


def _column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref).group(1)
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index - 1


def _excel_date(value: str) -> date | None:
    text = _clean(value)
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        serial = float(text)
        if serial > 30000:
            return (date(1899, 12, 30) + timedelta(days=int(serial)))
    for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = []
    for si in root.findall("a:si", NS):
        strings.append("".join(t.text or "" for t in si.findall(".//a:t", NS)))
    return strings


def _sheet_path(zf: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    for sheet in workbook.findall("a:sheets/a:sheet", NS):
        if sheet.attrib["name"] == sheet_name:
            rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = relmap[rel_id]
            return "xl/" + target if not target.startswith("xl/") else target
    raise ValueError(f"找不到工作表：{sheet_name}")


def _cell_value(cell, shared_strings: list[str]) -> str:
    value = cell.find("a:v", NS)
    text = value.text if value is not None else ""
    if cell.attrib.get("t") == "s" and text:
        return shared_strings[int(text)]
    if cell.attrib.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//a:t", NS))
    return text


def read_personnel_rows(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = _read_shared_strings(zf)
        root = ET.fromstring(zf.read(_sheet_path(zf, SHEET_NAME)))
    rows = []
    for row in root.findall("a:sheetData/a:row", NS):
        values = []
        for cell in row.findall("a:c", NS):
            index = _column_index(cell.attrib["r"])
            while len(values) <= index:
                values.append("")
            values[index] = _clean(_cell_value(cell, shared_strings))
        rows.append(values)
    if not rows:
        return []
    headers = rows[0]
    data = []
    for values in rows[1:]:
        item = {header: (values[index] if index < len(values) else "") for index, header in enumerate(headers)}
        if item.get("姓名"):
            data.append(item)
    return data


def _role_from_title(title: str) -> UserRole:
    if "主任" in title:
        return UserRole.director
    if "主管" in title:
        return UserRole.manager
    if "居督" in title or "督導" in title:
        return UserRole.supervisor
    return UserRole.caregiver


def _phone(*values: str) -> str | None:
    parts = [_clean(value) for value in values]
    return " / ".join(part for part in parts if part) or None


def _username_for(db, row: dict) -> str:
    base = row.get("工號") or row.get("身分證件號碼") or row["姓名"]
    username = re.sub(r"[^A-Za-z0-9_.-]", "", base) or f"pa{uuid4().hex[:8]}"
    candidate = username
    index = 1
    while db.query(User).filter(User.username == candidate).first():
        index += 1
        candidate = f"{username}_{index}"
    return candidate


def _find_user(db, row: dict) -> User | None:
    employee_no = row.get("工號")
    id_number = row.get("身分證件號碼")
    name = row.get("姓名")
    if employee_no:
        user = db.query(User).filter(User.employee_no == employee_no).first()
        if user:
            return user
        user = db.query(User).filter(User.username == employee_no).first()
        if user:
            return user
    if id_number:
        user = db.query(User).filter(User.id_number == id_number).first()
        if user:
            return user
    if name:
        return db.query(User).filter(User.display_name == name).first()
    return None


def import_personnel(path: Path, dry_run: bool = False) -> dict:
    rows = read_personnel_rows(path)
    db = SessionLocal()
    stats = {"rows": len(rows), "created": 0, "updated": 0, "active": 0, "inactive": 0}
    supervisor_links: list[tuple[User, str]] = []
    try:
        for row in rows:
            user = _find_user(db, row)
            if user:
                stats["updated"] += 1
            else:
                user = User(
                    username=_username_for(db, row),
                    display_name=row["姓名"],
                    password_hash=hash_password("imported-personnel-" + uuid4().hex),
                    role=_role_from_title(row.get("職稱", "")),
                )
                db.add(user)
                stats["created"] += 1

            user.display_name = row["姓名"]
            user.employee_no = row.get("工號") or None
            user.id_number = row.get("身分證件號碼") or None
            user.gender = row.get("性別") or None
            user.birth_date = _excel_date(row.get("出生年月日", ""))
            user.mobile = row.get("聯絡手機") or None
            user.phone = None
            user.email = user.email
            user.address = row.get("聯絡地址") or row.get("戶籍地址") or None
            user.job_title = row.get("職稱") or None
            user.role = _role_from_title(user.job_title or "")
            user.employment_status = row.get("狀態") or None
            user.is_active = row.get("狀態") == "在職"
            user.hire_date = _excel_date(row.get("到職日", ""))
            user.termination_date = _excel_date(row.get("離職生效日", ""))
            user.emergency_contact_name = row.get("緊急連絡人") or None
            user.emergency_contact_relation = row.get("緊急連絡人關係") or None
            user.emergency_contact_phone = _phone(row.get("緊急連絡人手機", ""), row.get("緊急連絡人市話", ""))
            user.note = f"戶籍地址：{row.get('戶籍地址')}" if row.get("戶籍地址") and row.get("戶籍地址") != user.address else None
            if row.get("直屬主管姓名"):
                supervisor_links.append((user, row["直屬主管姓名"]))
            if user.is_active:
                stats["active"] += 1
            else:
                stats["inactive"] += 1
            db.flush()

        for user, supervisor_name in supervisor_links:
            supervisor = db.query(User).filter(User.display_name == supervisor_name).first()
            if supervisor and supervisor.id != user.id:
                user.supervisor_id = supervisor.id

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
    apply_compatible_schema_updates()
    stats = import_personnel(args.file, dry_run=args.dry_run)
    print("DRY RUN" if args.dry_run else "IMPORTED")
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
