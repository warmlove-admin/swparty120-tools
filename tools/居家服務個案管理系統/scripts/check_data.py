import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.database import SessionLocal
from app.models.user import User, UserRole
from sqlalchemy import text

db = SessionLocal()

users = db.query(User).order_by(User.role, User.display_name).all()
result = {"users": [], "cases": []}
for u in users:
    result["users"].append({
        "id": u.id[:8],
        "name": u.display_name,
        "username": u.username,
        "role": u.role.value,
        "active": u.is_active,
        "sup_id": u.supervisor_id[:8] if u.supervisor_id else None,
    })

rows = db.execute(text("SELECT id, org_case_no, name, status FROM cases ORDER BY org_case_no")).fetchall()
for r in rows:
    result["cases"].append({"id": r[0][:8], "case_no": r[1], "name": r[2], "status": r[3]})

print(json.dumps(result, ensure_ascii=False, indent=2))
db.close()
