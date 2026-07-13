import sqlite3
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'app.db')
conn = sqlite3.connect(db_path)
rows = conn.execute(
    "SELECT display_name, employee_no, username, role, is_active "
    "FROM users ORDER BY display_name"
).fetchall()
print(f"Found {len(rows)} users:")
for r in rows:
    print(f"  {r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\tactive={r[4]}")
conn.close()
