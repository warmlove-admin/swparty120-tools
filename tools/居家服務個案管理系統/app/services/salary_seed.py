from app.database import SessionLocal
from app.models.salary_item import SalaryItem


DEFAULT_ITEMS = [
    # 應領項目 (earnings)
    {"name": "本薪（含交通）", "category": "earnings", "frequency": "monthly", "display_order": 1},
    {"name": "交通津貼", "category": "earnings", "frequency": "monthly", "display_order": 2},
    {"name": "油資補貼", "category": "earnings", "frequency": "monthly", "display_order": 3},
    {"name": "AA碼獎金", "category": "earnings", "frequency": "monthly", "display_order": 4},
    {"name": "久任獎金", "category": "earnings", "frequency": "semi_annual", "display_order": 5},
    {"name": "節金", "category": "earnings", "frequency": "irregular", "display_order": 6},
    {"name": "生日禮金", "category": "earnings", "frequency": "irregular", "display_order": 7},
    {"name": "考核獎金", "category": "earnings", "frequency": "irregular", "display_order": 8},
    {"name": "激勵獎金", "category": "earnings", "frequency": "irregular", "display_order": 9},
    {"name": "年終獎金", "category": "earnings", "frequency": "annual", "display_order": 10},
    # 應扣項目 (deductions)
    {"name": "勞工保險費", "category": "deductions", "frequency": "monthly", "display_order": 1},
    {"name": "全民健保費", "category": "deductions", "frequency": "monthly", "display_order": 2},
    {"name": "勞退自提", "category": "deductions", "frequency": "monthly", "display_order": 3},
    {"name": "請假扣薪", "category": "deductions", "frequency": "monthly", "display_order": 4},
]


def seed_salary_items_if_empty(db=None):
    if db is None:
        db = SessionLocal()
        close = True
    else:
        close = False
    try:
        existing = db.query(SalaryItem).count()
        if existing > 0:
            return
        for item in DEFAULT_ITEMS:
            db.add(SalaryItem(**item))
        db.commit()
    finally:
        if close:
            db.close()
