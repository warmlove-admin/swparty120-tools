"""Check role values in DB"""
import sqlite3, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'app.db')
conn = sqlite3.connect(db_path)
rows = conn.execute("SELECT employee_no, display_name, role FROM users WHERE employee_no LIKE 'W%'").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} | role={r[2]} | hex={r[2].encode('utf-8').hex() if r[2] else 'None'}")
conn.close()
