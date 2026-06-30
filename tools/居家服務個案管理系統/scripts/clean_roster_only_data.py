"""Reset the database to roster-derived data only."""

from __future__ import annotations

import argparse
from pathlib import Path

import xlrd
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.models.case import Case
from app.models.contact import Contact, EmergencyContact
from app.models.user import User
from scripts.import_case_summary_xls import _read_rows, import_cases


ROSTER_TABLES_TO_CLEAR = [
    "line_daily_analyses",
    "line_messages",
    "line_source_links",
    "line_groups",
    "contact_records",
    "complaint_progress_entries",
    "complaints",
    "service_schedules",
    "care_plan_assessment_links",
    "care_plan_goals",
    "care_plans",
    "goal_progress_logs",
    "goals",
    "assessment_items",
    "assessments",
    "cms_assessments",
    "caregiver_observations",
    "record_status_logs",
    "emergency_contacts",
    "contacts",
]


def _roster_summary(path: Path) -> tuple[set[str], set[str]]:
    rows = _read_rows(path)
    org_case_nos = {row["org_case_no"] for row in rows}
    supervisors = {row["supervisor"] for row in rows if row["supervisor"]}
    return org_case_nos, supervisors


def _count_table(db, table_name: str) -> int:
    return db.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()


def clean_roster_only(path: Path, dry_run: bool = False) -> dict:
    roster_case_nos, roster_supervisors = _roster_summary(path)
    db = SessionLocal()
    stats = {
        "roster_case_nos": len(roster_case_nos),
        "roster_supervisors": len(roster_supervisors),
        "deleted_rows": {},
        "deleted_cases_not_in_roster": 0,
        "deleted_users_not_roster_or_admin": 0,
    }
    try:
        for table_name in ROSTER_TABLES_TO_CLEAR:
            stats["deleted_rows"][table_name] = _count_table(db, table_name)
            db.execute(text(f"DELETE FROM {table_name}"))

        cases_not_in_roster = db.query(Case).filter(Case.org_case_no.notin_(roster_case_nos)).all()
        stats["deleted_cases_not_in_roster"] = len(cases_not_in_roster)
        for case in cases_not_in_roster:
            db.delete(case)
        db.flush()

        keep_usernames = {"admin"}
        delete_users = [
            user for user in db.query(User).all()
            if user.username not in keep_usernames and user.display_name not in roster_supervisors
        ]
        stats["deleted_users_not_roster_or_admin"] = len(delete_users)
        for user in delete_users:
            db.delete(user)
        db.flush()

        if dry_run:
            db.rollback()
            return stats

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    import_stats = import_cases(path, dry_run=False)
    stats["reimport"] = import_stats
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    stats = clean_roster_only(args.file, dry_run=args.dry_run)
    print("DRY RUN" if args.dry_run else "CLEANED")
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
