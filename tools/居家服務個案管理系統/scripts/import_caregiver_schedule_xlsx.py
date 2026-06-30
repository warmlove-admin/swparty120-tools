from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models.case import Case, CaseStatus  # noqa: E402
from app.models.caregiver_service_record import CaregiverServiceRecord  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402


NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
DATE_CELL_RE = re.compile(r"^\d{2}/\d{2}$")
HEADER_MONTH_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月")
TIME_RE = re.compile(r"^(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})\s+(.+)$")
SERVICE_CODE_RE = re.compile(r"\b(?:BA|GA|SC|G|B)\d[\w\-]*x?\d*", re.IGNORECASE)


@dataclass
class ParsedVisit:
    source_file: str
    caregiver_name: str
    service_date: date
    start_time: time
    end_time: time
    minutes: int
    case_name: str
    service_codes: str


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("a:si", NS):
        values.append("".join(text.text or "" for text in item.findall(".//a:t", NS)))
    return values


def column_index(ref: str) -> int:
    match = re.match(r"([A-Z]+)", ref)
    if not match:
        return 0
    index = 0
    for letter in match.group(1):
        index = index * 26 + ord(letter) - 64
    return index - 1


def cell_value(cell, shared_strings: list[str]) -> str:
    value = cell.find("a:v", NS)
    text = value.text if value is not None else ""
    if cell.attrib.get("t") == "s" and text:
        return shared_strings[int(text)]
    if cell.attrib.get("t") == "inlineStr":
        return "".join(item.text or "" for item in cell.findall(".//a:t", NS))
    return text


def first_sheet(zf: zipfile.ZipFile) -> tuple[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    sheet = workbook.findall("a:sheets/a:sheet", NS)[0]
    rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
    target = relmap[rel_id]
    path = "xl/" + target if not target.startswith("xl/") else target
    return sheet.attrib["name"], path


def read_rows(path: str) -> tuple[str, list[list[str]]]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        sheet_name, sheet_path = first_sheet(zf)
        root = ET.fromstring(zf.read(sheet_path))
        rows: list[list[str]] = []
        for row in root.findall("a:sheetData/a:row", NS):
            values: list[str] = []
            for cell in row.findall("a:c", NS):
                index = column_index(cell.attrib["r"])
                while len(values) <= index:
                    values.append("")
                values[index] = cell_value(cell, shared_strings).strip()
            rows.append(values)
        return sheet_name, rows


def parse_report_month(rows: list[list[str]], fallback_year: int, fallback_month: int) -> tuple[int, int]:
    for row in rows[:4]:
        for value in row:
            match = HEADER_MONTH_RE.search(value)
            if match:
                return int(match.group(1)), int(match.group(2))
    return fallback_year, fallback_month


def parse_caregiver_name(sheet_name: str, rows: list[list[str]]) -> str:
    for row in rows[:5]:
        joined = " ".join(value for value in row if value)
        if "員工姓名" in joined:
            name = re.sub(r"^.*員工姓名[：:]\s*", "", joined).strip()
            return name.split(" - ")[-1].strip()
    return sheet_name.split(" - ")[-1].strip()


def parse_time(value: str) -> time:
    return datetime.strptime(value.zfill(5), "%H:%M").time()


def minutes_between(start: time, end: time) -> int:
    start_dt = datetime.combine(date(2000, 1, 1), start)
    end_dt = datetime.combine(date(2000, 1, 1), end)
    return int((end_dt - start_dt).total_seconds() // 60)


def parse_visit_text(text: str) -> tuple[time, time, str, str] | None:
    match = TIME_RE.match(" ".join(text.split()))
    if not match:
        return None
    start = parse_time(match.group(1))
    end = parse_time(match.group(2))
    rest = match.group(3).strip()
    code_match = SERVICE_CODE_RE.search(rest)
    if code_match:
        case_name = rest[:code_match.start()].strip()
        service_codes = rest[code_match.start():].strip()
    else:
        parts = rest.split(maxsplit=1)
        case_name = parts[0].strip()
        service_codes = parts[1].strip() if len(parts) > 1 else ""
    if not case_name:
        return None
    return start, end, case_name, service_codes


def parse_workbook(path: str, import_year: int, import_month: int) -> list[ParsedVisit]:
    sheet_name, rows = read_rows(path)
    year, month = parse_report_month(rows, import_year, import_month)
    caregiver_name = parse_caregiver_name(sheet_name, rows)
    visits: list[ParsedVisit] = []
    date_rows: list[tuple[int, dict[int, date]]] = []
    for row_index, row in enumerate(rows):
        dates: dict[int, date] = {}
        for col_index, value in enumerate(row):
            if DATE_CELL_RE.match(value):
                parsed_month, parsed_day = (int(part) for part in value.split("/"))
                parsed_year = year
                if month == 1 and parsed_month == 12:
                    parsed_year -= 1
                elif month == 12 and parsed_month == 1:
                    parsed_year += 1
                dates[col_index] = date(parsed_year, parsed_month, parsed_day)
        if dates:
            date_rows.append((row_index, dates))

    for block_index, (row_index, dates) in enumerate(date_rows):
        next_row_index = date_rows[block_index + 1][0] if block_index + 1 < len(date_rows) else len(rows)
        for data_row in rows[row_index + 1:next_row_index]:
            for col_index, service_date in dates.items():
                if service_date.year != import_year or service_date.month != import_month:
                    continue
                if col_index >= len(data_row):
                    continue
                cell_text = data_row[col_index].strip()
                if not cell_text:
                    continue
                for line in [part.strip() for part in cell_text.splitlines() if part.strip()]:
                    parsed = parse_visit_text(line)
                    if not parsed:
                        continue
                    start, end, case_name, service_codes = parsed
                    visits.append(ParsedVisit(
                        source_file=os.path.basename(path),
                        caregiver_name=caregiver_name,
                        service_date=service_date,
                        start_time=start,
                        end_time=end,
                        minutes=minutes_between(start, end),
                        case_name=case_name,
                        service_codes=service_codes,
                    ))
    return visits


def case_sort_key(case: Case) -> tuple[int, int]:
    if case.status == CaseStatus.active:
        status_rank = 0
    elif case.status == CaseStatus.paused:
        status_rank = 1
    else:
        status_rank = 2
    org_case_no = case.org_case_no or ""
    numeric = int(org_case_no) if org_case_no.isdigit() else -1
    return status_rank, -numeric


def find_case(db, name: str) -> Case | None:
    matches = db.query(Case).filter(Case.name == name).all()
    if not matches:
        return None
    return sorted(matches, key=case_sort_key)[0]


def import_visits(pattern: str, import_year: int, import_month: int, dry_run: bool, replace_month: bool) -> int:
    paths = sorted(glob.glob(pattern))
    if not paths:
        print(f"找不到檔案：{pattern}")
        return 1
    Base.metadata.create_all(bind=engine)
    all_visits: list[ParsedVisit] = []
    for path in paths:
        all_visits.extend(parse_workbook(path, import_year, import_month))

    db = SessionLocal()
    try:
        caregiver_by_name = {
            user.display_name: user
            for user in db.query(User).filter(User.role == UserRole.caregiver).all()
        }
        unmatched_caregivers = Counter()
        unmatched_cases = Counter()
        matched: list[tuple[ParsedVisit, Case, User]] = []
        for visit in all_visits:
            caregiver = caregiver_by_name.get(visit.caregiver_name)
            case = find_case(db, visit.case_name)
            if not caregiver:
                unmatched_caregivers[visit.caregiver_name] += 1
            if not case:
                unmatched_cases[visit.case_name] += 1
            if caregiver and case:
                matched.append((visit, case, caregiver))

        print(f"讀取檔案：{len(paths)}")
        print(f"解析班表筆數：{len(all_visits)}")
        print(f"可匯入筆數：{len(matched)}")
        if unmatched_caregivers:
            print("未對應居服員：")
            for name, count in unmatched_caregivers.most_common():
                print(f"  {name}: {count}")
        if unmatched_cases:
            print("未對應個案：")
            for name, count in unmatched_cases.most_common(30):
                print(f"  {name}: {count}")
            if len(unmatched_cases) > 30:
                print(f"  ...另 {len(unmatched_cases) - 30} 位")
        if dry_run:
            print("dry-run：未寫入資料庫")
            return 0

        if replace_month:
            month_start = date(import_year, import_month, 1)
            month_end = date(import_year + (1 if import_month == 12 else 0), 1 if import_month == 12 else import_month + 1, 1)
            deleted = (
                db.query(CaregiverServiceRecord)
                .filter(
                    CaregiverServiceRecord.service_date >= month_start,
                    CaregiverServiceRecord.service_date < month_end,
                )
                .delete(synchronize_session=False)
            )
            print(f"已刪除同月份舊班表紀錄：{deleted}")

        for visit, case, caregiver in matched:
            db.add(CaregiverServiceRecord(
                case_id=case.id,
                caregiver_id=caregiver.id,
                service_date=visit.service_date,
                start_time=visit.start_time,
                end_time=visit.end_time,
                minutes=visit.minutes,
                case_name_raw=visit.case_name,
                caregiver_name_raw=visit.caregiver_name,
                service_codes=visit.service_codes,
                source_file=visit.source_file,
            ))
        db.commit()
        print(f"已匯入班表紀錄：{len(matched)}")
        return 0
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="匯入官方系統員工排班表 XLSX")
    parser.add_argument("pattern", nargs="?", help="XLSX 路徑或萬用字元；未提供時讀取下載資料夾的居服員7月班表")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace-month", action="store_true", help="寫入前刪除同年月既有匯入紀錄")
    args = parser.parse_args()
    pattern = args.pattern
    if not pattern:
        folder_name = "".join(chr(code) for code in [23621, 26381, 21729, 55, 26376, 29677, 34920])
        pattern = str(Path.home() / "Downloads" / folder_name / "*.xlsx")
    return import_visits(pattern, args.year, args.month, args.dry_run, args.replace_month)


if __name__ == "__main__":
    raise SystemExit(main())
