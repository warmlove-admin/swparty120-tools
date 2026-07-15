"""
Import Apollo insurance data v2 - Updates both users table and employee_changes
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from datetime import date
from app.database import get_db
from app.models.user import User
from app.models.employee_change import EmployeeChange

db = next(get_db())

with open("scripts/apollo_insurance_data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

today = date.today()
updated = 0
changes_created = 0

# First, clear ALL existing insurance change records (we're replacing with fresh Apollo data)
old_changes = db.query(EmployeeChange).filter(
    EmployeeChange.change_type == "insurance"
).all()
for c in old_changes:
    db.delete(c)
print(f"Deleted {len(old_changes)} old insurance change records")

for record in data:
    emp_num = record["emp_number"]
    user = db.query(User).filter(User.employee_no == emp_num, User.role == "caregiver").first()
    if not user:
        print(f"  SKIP: {emp_num} not found in users table")
        continue

    # Map Apollo fields to User columns
    mapping = {
        "insurance_labor_amount": record["labor_insurance_amount"],
        "insurance_occupational_amount": record["occupational_insurance_amount"],
        "insurance_labor_pension_amount": record["labor_pension_amount"],
        "labor_pension_employer_rate": record["labor_pension_employer_rate"],
        "labor_pension_personal_rate": record["labor_pension_personal_rate"],
        "insurance_health_amount": record["health_insurance_amount"],
    }

    print(f"\n{emp_num} {record['name']}:")
    for field, new_val in mapping.items():
        old_val = getattr(user, field, 0) or 0
        if old_val != new_val:
            print(f"  {field}: {old_val} -> {new_val}")
            # Update user
            setattr(user, field, new_val)
            # Create change record
            change = EmployeeChange(
                employee_id=user.id,
                change_type="insurance",
                field_name=field,
                effective_date=today,
                old_value=old_val,
                new_value=new_val,
                source="apollo_import",
                created_by=user.id,  # System import
            )
            db.add(change)
            changes_created += 1
        else:
            print(f"  {field}: {new_val} (unchanged)")

    updated += 1

db.commit()
print(f"\n{'='*60}")
print(f"Updated {updated} employees")
print(f"Created {changes_created} change records")

# Verify
print(f"\n{'='*60}")
print("Verification - checking key employees:")
for emp_num in ["W084", "W079", "W083", "W089", "W068", "W021"]:
    user = db.query(User).filter(User.employee_no == emp_num).first()
    if user:
        print(f"  {emp_num} {user.display_name}: labor={user.insurance_labor_amount}, health={user.insurance_health_amount}")
