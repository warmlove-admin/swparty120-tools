"""One-time: add insurance columns to users table"""
import sqlite3, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'app.db')
conn = sqlite3.connect(db_path)

cols_to_add = [
    ("insurance_labor_amount", "INTEGER DEFAULT 0"),
    ("insurance_occupational_amount", "INTEGER DEFAULT 0"),
    ("insurance_labor_pension_amount", "INTEGER DEFAULT 0"),
    ("labor_pension_employer_rate", "INTEGER DEFAULT 6"),
    ("labor_pension_personal_rate", "INTEGER DEFAULT 0"),
    ("insurance_health_amount", "INTEGER DEFAULT 0"),
    ("health_dependents", "INTEGER DEFAULT 0"),
    ("insurance_effective_year", "INTEGER DEFAULT 0"),
    ("insurance_effective_month", "INTEGER DEFAULT 0"),
]

existing = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
for col_name, col_type in cols_to_add:
    if col_name not in existing:
        conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        print(f"  Added: {col_name}")
    else:
        print(f"  Already exists: {col_name}")

conn.commit()
conn.close()
print("Done!")
