"""執行 LINE 每日對話分析。

可由 Windows 工作排程在每日下班後呼叫：
  .\\.venv\\Scripts\\python.exe scripts\\run_line_daily_analysis.py
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: F401,E402
from app.models.user import User, UserRole  # noqa: E402
from app.services.line_daily_analysis import run_daily_line_analysis  # noqa: E402


def _find_actor(db, username: str | None):
    if username:
        actor = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
        if actor:
            return actor
    return (
        db.query(User)
        .filter(User.role.in_([UserRole.manager, UserRole.director, UserRole.supervisor]), User.is_active.is_(True))
        .order_by(User.created_at.asc())
        .first()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="分析日期 YYYY-MM-DD；預設為昨天")
    parser.add_argument("--username", help="建立紀錄用的系統執行帳號；預設取第一個主管/主任/居督")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        actor = _find_actor(db, args.username)
        if not actor:
            raise SystemExit("找不到可用的執行帳號，請先建立主管、主任或居督帳號。")
        result = run_daily_line_analysis(db, target_date, actor)
        db.commit()
        print(
            f"{target_date} LINE每日分析完成："
            f"產生 {result.created} 筆，略過 {result.skipped} 組，訊息群組 {result.message_groups} 組。"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
