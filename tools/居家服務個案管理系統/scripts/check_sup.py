import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.database import SessionLocal
from app.models.user import User
from sqlalchemy import text

db = SessionLocal()

users = {u.id: u for u in db.query(User).all()}

for uid, u in users.items():
    sup_name = users.get(u.supervisor_id).display_name if u.supervisor_id and u.supervisor_id in users else "N/A"
    print(f"{u.username:15s} | {u.display_name:8s} | {u.role.value:5s} | sup={sup_name}")

print("\n--- Check caregiver1 ---")
cg = db.query(User).filter(User.username == "caregiver1").first()
if cg:
    print(f"caregiver1 exists: {cg.display_name}, sup_id={cg.supervisor_id}")
else:
    print("caregiver1 does NOT exist")

print("\n--- Check W063 (李郁萱) password ---")
w063 = db.query(User).filter(User.username == "W063").first()
if w063:
    print(f"W063 exists: {w063.display_name}, role={w063.role.value}, sup_id={w063.supervisor_id}")
    from app.auth import hash_password, verify_password
    print(f"Password check for 'Test1234': {verify_password('Test1234', w063.password_hash)}")
else:
    print("W063 not found")

db.close()
