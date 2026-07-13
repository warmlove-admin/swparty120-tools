"""
Import Apollo insurance data into the database.
Run after apollo_insurance_scraper.py has generated apollo_insurance_data.json.
"""
import sqlite3
import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'app.db')
json_path = os.path.join(os.path.dirname(__file__), 'apollo_insurance_data.json')

with open(json_path, 'r', encoding='utf-8') as f:
    insurance_data = json.load(f)

conn = sqlite3.connect(db_path)
updated = 0
not_found = []

for record in insurance_data:
    emp_num = record["emp_number"]
    cursor = conn.execute(
        "UPDATE users SET "
        "insurance_labor_amount = ?, "
        "insurance_occupational_amount = ?, "
        "insurance_labor_pension_amount = ?, "
        "labor_pension_employer_rate = ?, "
        "labor_pension_personal_rate = ?, "
        "insurance_health_amount = ?, "
        "insurance_effective_year = 2025, "
        "insurance_effective_month = 10 "
        "WHERE employee_no = ? AND role = 'caregiver'",
        (
            record["labor_insurance_amount"],
            record["occupational_insurance_amount"],
            record["labor_pension_amount"],
            record["labor_pension_employer_rate"],
            record["labor_pension_personal_rate"],
            record["health_insurance_amount"],
            emp_num,
        )
    )
    if cursor.rowcount > 0:
        updated += 1
        print(f"  {emp_num} {record['name']}: 勞保={record['labor_insurance_amount']}, "
              f"職保={record['occupational_insurance_amount']}, "
              f"勞退={record['labor_pension_amount']}, "
              f"健保={record['health_insurance_amount']}")
    else:
        not_found.append(f"{emp_num} {record['name']}")

conn.commit()
conn.close()

print(f"\nUpdated {updated} employees")
if not_found:
    print(f"Not found in DB: {not_found}")
