from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path

from app.database import SessionLocal
from app.models.case import Case, CaseStatus, CloseReasonType, PauseReasonType
from scripts.import_case_summary_xls import _read_rows, _status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=Path.home() / "Downloads" / "案量統計20260627.xls")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    rows = _read_rows(args.file)
    db = SessionLocal()
    mismatches = []
    try:
        for row in rows:
            case = db.query(Case).filter(Case.org_case_no == row["org_case_no"]).first()
            if not case:
                continue
            expected = _status(row)
            if case.status != expected:
                mismatches.append((case, expected, row))
        print(f"rows: {len(rows)}")
        print(f"mismatches: {len(mismatches)}")
        print(Counter(f"{case.status.value}->{expected.value}" for case, expected, _ in mismatches))
        for case, expected, row in mismatches[:80]:
            print(f"{case.org_case_no} {case.name}: {case.status.value} -> {expected.value} | source_status={row['service_status']} pause={row['pause_date']} close={row['close_date']}")
        if args.apply:
            for case, expected, row in mismatches:
                case.status = expected
                if expected == CaseStatus.active:
                    case.resume_date = date.today() if case.pause_date else case.resume_date
                    case.close_date = None
                    case.close_reason_type = None
                    case.close_reason_note = None
                elif expected == CaseStatus.paused:
                    case.pause_date = row["pause_date"]
                    case.pause_reason_type = PauseReasonType.other if row["pause_reason"] or row["pause_note"] else None
                    case.pause_reason_note = " / ".join(part for part in [row["pause_reason"], row["pause_note"]] if part) or None
                    case.resume_date = None
                    case.close_date = None
                    case.close_reason_type = None
                    case.close_reason_note = None
                elif expected == CaseStatus.closed:
                    case.close_date = row["close_date"]
                    case.close_reason_type = CloseReasonType.other if row["close_reason"] or row["close_note"] else None
                    case.close_reason_note = " / ".join(part for part in [row["close_reason"], row["close_note"]] if part) or None
            db.commit()
            print(f"applied: {len(mismatches)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
