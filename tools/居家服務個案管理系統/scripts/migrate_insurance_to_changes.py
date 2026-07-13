"""
Migrate insurance data from User table to employee_changes table.
Also creates the employee_changes table if it doesn't exist.
"""
import sqlite3
import json
import os
import sys
import io
from datetime import date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'app.db')
json_path = os.path.join(os.path.dirname(__file__), 'apollo_insurance_data.json')

conn = sqlite3.connect(db_path)

# 1) Create employee_changes table
conn.execute("""
CREATE TABLE IF NOT EXISTS employee_changes (
    id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    field_name TEXT NOT NULL,
    effective_date DATE NOT NULL,
    old_value INTEGER DEFAULT 0,
    new_value INTEGER DEFAULT 0,
    source TEXT DEFAULT 'manual',
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (employee_id) REFERENCES users(id)
)
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_ec_emp ON employee_changes(employee_id)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_ec_field ON employee_changes(field_name)")
print("Table employee_changes ready")

# 2) Read Apollo data
with open(json_path, 'r', encoding='utf-8') as f:
    apollo_data = json.load(f)

# 3) For each employee, read current User table values and insert into employee_changes
import uuid

FIELD_MAP = [
    ("labor_insurance_amount", "insurance_labor_amount"),
    ("occupational_insurance_amount", "insurance_occupational_amount"),
    ("labor_pension_amount", "insurance_labor_pension_amount"),
    ("labor_pension_employer_rate", "labor_pension_employer_rate"),
    ("labor_pension_personal_rate", "labor_pension_personal_rate"),
    ("health_insurance_amount", "insurance_health_amount"),
]

inserted = 0
for record in apollo_data:
    emp_num = record["emp_number"]
    # Find employee_id
    row = conn.execute("SELECT id FROM users WHERE employee_no = ?", (emp_num,)).fetchone()
    if not row:
        print(f"  SKIP {emp_num}: not in DB")
        continue
    emp_id = row[0]

    for apollo_field, db_field in FIELD_MAP:
        new_val = record.get(apollo_field, 0)
        if new_val == 0:
            continue  # skip zeros (e.g. 楊玉玲健保=0)
        effective = date(2025, 10, 1)  # Apollo 資料的生效月
        conn.execute(
            "INSERT INTO employee_changes (id, employee_id, change_type, field_name, effective_date, old_value, new_value, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, 'apollo_import', datetime('now'))",
            (str(uuid.uuid4()), emp_id, "insurance", db_field, effective.isoformat(), new_val)
        )
        inserted += 1

    # Also migrate hourly_wage from User table
    wage_row = conn.execute("SELECT hourly_wage FROM users WHERE id = ?", (emp_id,)).fetchone()
    if wage_row and wage_row[0] and int(wage_row[0]) > 0:
        conn.execute(
            "INSERT INTO employee_changes (id, employee_id, change_type, field_name, effective_date, old_value, new_value, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, 'apollo_import', datetime('now'))",
            (str(uuid.uuid4()), emp_id, "salary", "hourly_wage", date(2025, 1, 1).isoformat(), wage_row[0])
        )
        inserted += 1

conn.commit()
conn.close()
print(f"Inserted {inserted} change records")
