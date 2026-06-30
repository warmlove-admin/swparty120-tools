import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.database import SessionLocal
from app.models.user import User
from app.auth import hash_password

PW = "test1234"

db = SessionLocal()
targets = ["W063", "W081", "W012", "W001", "W002"]
for username in targets:
    u = db.query(User).filter(User.username == username).first()
    if u:
        u.password_hash = hash_password(PW)
        print(f"  Set password for {u.username} ({u.display_name})")
    else:
        print(f"  User {username} not found")
db.commit()
db.close()
print("Done")
