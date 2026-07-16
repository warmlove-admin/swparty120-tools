"""2026 年（民國 115 年）勞保、健保、勞退費率與級距表。"""

# ── 勞保投保薪資分級表（2026.01.01 起適用）─────────────────────────────────
# (月薪資總額上限, 月投保薪資)
# 含部分工時勞工級距（11,100 起）
LABOR_INSURANCE_GRADES = [
    (11_100, 11_100),
    (12_540, 12_540),
    (13_500, 13_500),
    (15_840, 15_840),
    (16_500, 16_500),
    (17_280, 17_280),
    (17_880, 17_880),
    (19_047, 19_047),
    (20_008, 20_008),
    (21_009, 21_009),
    (22_000, 22_000),
    (23_100, 23_100),
    (24_000, 24_000),
    (25_250, 25_250),
    (26_400, 26_400),
    (27_600, 27_600),
    (28_590, 28_590),
    (29_500, 29_500),
    (30_300, 30_300),
    (31_800, 31_800),
    (33_300, 33_300),
    (34_800, 34_800),
    (36_300, 36_300),
    (38_200, 38_200),
    (40_100, 40_100),
    (42_000, 42_000),
    (43_900, 43_900),
    (45_800, 45_800),  # 最高
]

# 勞保費率 12.5%（普通事故 11.5% + 就保 1%），勞工負擔 20%
LABOR_INSURANCE_RATE = 0.125
LABOR_INSURANCE_EMPLOYEE_RATIO = 0.20
# 職災保險費率（假設一般行業 0.2%，雇主全額負擔）
OCCUPATIONAL_INJURY_RATE = 0.002

# ── 健保投保金額分級表（2026.01.01 起適用）─────────────────────────────────
# (月薪資總額上限, 月投保金額)
# 健保最低級距為29,500
HEALTH_INSURANCE_GRADES = [
    (29_500, 29_500),
    (30_300, 30_300),
    (31_800, 31_800),
    (33_300, 33_300),
    (34_800, 34_800),
    (36_300, 36_300),
    (38_200, 38_200),
    (40_100, 40_100),
    (42_000, 42_000),
    (43_900, 43_900),
    (45_800, 45_800),
    (48_200, 48_200),
    (50_600, 50_600),
    (53_000, 53_000),
    (55_400, 55_400),
    (57_800, 57_800),
    (60_800, 60_800),
    (63_800, 63_800),
    (66_800, 66_800),
    (69_800, 69_800),
    (72_800, 72_800),
    (76_500, 76_500),
    (80_200, 80_200),
    (83_900, 83_900),
    (87_600, 87_600),
    (92_100, 92_100),
    (96_600, 96_600),
    (101_100, 101_100),
    (105_600, 105_600),
    (110_100, 110_100),
    (115_500, 115_500),
    (120_900, 120_900),
    (126_300, 126_300),
    (131_700, 131_700),
    (137_100, 137_100),
    (142_500, 142_500),
    (147_900, 147_900),
    (150_000, 150_000),
    (156_400, 156_400),
    (162_800, 162_800),
    (169_200, 169_200),
    (175_600, 175_600),
    (182_000, 182_000),
    (189_500, 189_500),
    (197_000, 197_000),
    (204_500, 204_500),
    (212_000, 212_000),
    (219_500, 219_500),
    (228_200, 228_200),
    (236_900, 236_900),
    (245_600, 245_600),
    (254_300, 254_300),
    (263_000, 263_000),
    (273_000, 273_000),
    (283_000, 283_000),
    (293_000, 293_000),
    (303_000, 303_000),
    (313_000, 313_000),
]

# 健保費率 5.17%，被保險人負擔 30%，投保單位負擔 60%，政府補助 10%
HEALTH_INSURANCE_RATE = 0.0517
HEALTH_INSURANCE_EMPLOYEE_RATIO = 0.30
HEALTH_INSURANCE_EMPLOYER_RATIO = 0.60
HEALTH_INSURANCE_GOVT_RATIO = 0.10

# ── 勞退月提繳工資分級表（2026.01.01 起適用）───────────────────────────────
# (月薪資總額上限, 月提繳工資)
LABOR_PENSION_GRADES = [
    (1_500, 1_500),
    (3_000, 3_000),
    (4_500, 4_500),
    (6_000, 6_000),
    (7_500, 7_500),
    (8_700, 8_700),
    (9_900, 9_900),
    (11_100, 11_100),
    (12_540, 12_540),
    (13_500, 13_500),
    (15_840, 15_840),
    (16_500, 16_500),
    (17_280, 17_280),
    (17_880, 17_880),
    (19_047, 19_047),
    (20_008, 20_008),
    (21_009, 21_009),
    (22_000, 22_000),
    (23_100, 23_100),
    (24_000, 24_000),
    (25_250, 25_250),
    (26_400, 26_400),
    (27_600, 27_600),
    (28_590, 28_590),
    (29_500, 29_500),
    (30_300, 30_300),
    (31_800, 31_800),
    (33_300, 33_300),
    (34_800, 34_800),
    (36_300, 36_300),
    (38_200, 38_200),
    (40_100, 40_100),
    (42_000, 42_000),
    (43_900, 43_900),
    (45_800, 45_800),
    (48_200, 48_200),
    (50_600, 50_600),
    (53_000, 53_000),
    (55_400, 55_400),
    (57_800, 57_800),
    (60_800, 60_800),
    (63_800, 63_800),
    (66_800, 66_800),
    (69_800, 69_800),
    (72_800, 72_800),
    (76_500, 76_500),
    (80_200, 80_200),
    (83_900, 83_900),
    (87_600, 87_600),
    (92_100, 92_100),
    (96_600, 96_600),
    (101_100, 101_100),
    (105_600, 105_600),
    (110_100, 110_100),
    (115_500, 115_500),
    (120_900, 120_900),
    (126_300, 126_300),
    (131_700, 131_700),
    (137_100, 137_100),
    (142_500, 142_500),
    (147_900, 147_900),
    (150_000, 150_000),
]

# 勞退提繳率：雇主強制 6%，員工自願 0~6%
LABOR_PENSION_RATE = 0.06


# ── 級距查詢 ──────────────────────────────────────────────────────────────────

def _lookup_grade(grades: list, salary: int) -> int:
    """依薪資找到對應的投保/提繳級距金額。"""
    if salary <= 0:
        return grades[0][1]
    for upper, grade_amount in grades:
        if salary <= upper:
            return grade_amount
    return grades[-1][1]


def lookup_labor_insurance_grade(salary: int) -> int:
    return _lookup_grade(LABOR_INSURANCE_GRADES, salary)


def lookup_health_insurance_grade(salary: int) -> int:
    return _lookup_grade(HEALTH_INSURANCE_GRADES, salary)


def lookup_labor_pension_grade(salary: int) -> int:
    return _lookup_grade(LABOR_PENSION_GRADES, salary)


# ── 自付額計算 ────────────────────────────────────────────────────────────────

def calc_labor_insurance_self_pay(grade_amount: int) -> int:
    """勞保每月自付額 = 投保級距 × 12.5% × 20%（無條件捨去）"""
    return int(grade_amount * LABOR_INSURANCE_RATE * LABOR_INSURANCE_EMPLOYEE_RATIO)


def calc_health_insurance_self_pay(grade_amount: int) -> int:
    """健保每月本人自付額 = 投保級距 × 5.17% × 30%"""
    return round(grade_amount * HEALTH_INSURANCE_RATE * HEALTH_INSURANCE_EMPLOYEE_RATIO)


def calc_health_insurance_dependent_pay(grade_amount: int, dependents: list) -> int:
    """健保每月眷屬自付額合計。
    每位眷屬：本人級距自付額 × (1 - 補助比例)
    未勾選補助的眷屬：本人級距自付額
    """
    per_person = calc_health_insurance_self_pay(grade_amount)
    total = 0
    for dep in dependents:
        if not dep.is_active:
            continue
        if dep.has_exemption and dep.subsidy_rate > 0:
            total += per_person * (100 - dep.subsidy_rate) / 100
        else:
            total += per_person
    return total


def calc_health_insurance_subsidy(grade_amount: int, dependents: list) -> int:
    """計算眷屬補助總金額。
    補助金額 = 本人級距自付額 × 補助比例
    """
    per_person = calc_health_insurance_self_pay(grade_amount)
    total_subsidy = 0
    for dep in dependents:
        if not dep.is_active or not dep.has_exemption or dep.subsidy_rate <= 0:
            continue
        subsidy = per_person * dep.subsidy_rate / 100
        if dep.max_subsidy_amount > 0:
            subsidy = min(subsidy, dep.max_subsidy_amount)
        total_subsidy += subsidy
    return total_subsidy


def calc_labor_pension_self_pay(grade_amount: int, self_rate_pct: int = 0) -> int:
    """勞退每月自提金額 = 提繳工資 × 自提率%"""
    return int(grade_amount * (self_rate_pct or 0) / 100)


def calc_total_employee_deduction(
    labor_grade: int,
    health_grade: int,
    health_dependents: int,
    labor_pension_self_rate: int,
) -> dict:
    """計算員工每月應扣合計。"""
    li = calc_labor_insurance_self_pay(labor_grade)
    hi = calc_health_insurance_self_pay(health_grade, health_dependents)
    lp = calc_labor_pension_self_pay(health_grade, labor_pension_self_rate)
    return {
        "labor_insurance_self": li,
        "health_insurance_self": hi,
        "labor_pension_self": lp,
        "total_deduction": li + hi + lp,
    }


# ── 雇主負擔計算（公司頁面用）──────────────────────────────────────────────

def calc_labor_insurance_employer_pay(grade_amount: int) -> int:
    """勞保每月雇主負擔 = 投保級距 × 12.5% × 70%"""
    return int(grade_amount * LABOR_INSURANCE_RATE * 0.70)


def calc_health_insurance_employer_pay(grade_amount: int, dependents: int = 0) -> int:
    """健保每月投保單位負擔 = 投保級距 × 5.17% × 60% × (1 + min(眷屬, 3))
    每人分別四捨五入後加總。"""
    dep = min(dependents or 0, 3)
    per_person = round(grade_amount * HEALTH_INSURANCE_RATE * HEALTH_INSURANCE_EMPLOYER_RATIO)
    return per_person * (1 + dep)


def calc_labor_pension_employer_pay(grade_amount: int, employer_rate_pct: int = 6) -> int:
    """勞退每月雇主提繳 = 提繳工資 × 雇主提繳率%"""
    return int(grade_amount * (employer_rate_pct or 6) / 100)


def calc_occupational_injury_pay(grade_amount: int) -> int:
    """職災保險每月雇主負擔 = 投保級距 × 職災費率（0.2%）"""
    return int(grade_amount * OCCUPATIONAL_INJURY_RATE)


def calc_employer_monthly_cost(
    labor_grade: int,
    occupational_grade: int,
    labor_pension_grade: int,
    labor_pension_employer_rate: int,
    health_grade: int,
    health_dependents: int,
) -> dict:
    """計算雇主每月負擔合計。"""
    li = calc_labor_insurance_employer_pay(labor_grade)
    oi = calc_occupational_injury_pay(occupational_grade)
    lp = calc_labor_pension_employer_pay(labor_pension_grade, labor_pension_employer_rate)
    hi = calc_health_insurance_employer_pay(health_grade, health_dependents)
    return {
        "labor_insurance_employer": li,
        "occupational_injury_employer": oi,
        "labor_pension_employer": lp,
        "health_insurance_employer": hi,
        "total_employer_cost": li + oi + lp + hi,
    }
