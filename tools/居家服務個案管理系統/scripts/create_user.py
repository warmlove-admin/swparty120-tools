"""建立使用者帳號（命令列工具，給主管/管理者建立帳號用）。

用法範例：
  python scripts/create_user.py --username admin --display-name 王主管 --role 主管
執行後會要求輸入密碼（不會顯示在畫面上）。
"""
import argparse
import getpass
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.auth import hash_password  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    parser.add_argument(
        "--password",
        help="非互動模式直接指定密碼（僅建議測試環境使用，正式環境請省略此參數改用互動輸入）",
    )
    args = parser.parse_args()

    if args.password:
        password = args.password
    else:
        password = getpass.getpass("請輸入密碼：")
        password_confirm = getpass.getpass("請再輸入一次密碼：")
        if password != password_confirm:
            print("兩次密碼不一致，已取消。")
            return

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == args.username).first():
            print(f"帳號 {args.username} 已存在，已取消。")
            return

        user = User(
            username=args.username,
            display_name=args.display_name,
            role=UserRole(args.role),
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        print(f"已建立帳號：{args.username}（{args.display_name} / {args.role}）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
