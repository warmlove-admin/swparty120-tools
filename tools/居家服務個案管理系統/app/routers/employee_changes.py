from datetime import date, datetime
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.user import User, UserRole
from app.models.employee_change import EmployeeChange
from app.models.nhi_dependent import NhiDependent
from app.services.insurance import (
    LABOR_INSURANCE_GRADES,
    HEALTH_INSURANCE_GRADES,
    LABOR_PENSION_GRADES,
    calc_labor_insurance_self_pay,
    calc_health_insurance_self_pay,
    calc_health_insurance_dependent_pay,
    calc_health_insurance_subsidy,
    calc_labor_pension_self_pay,
    calc_total_employee_deduction,
    lookup_labor_insurance_grade,
    lookup_health_insurance_grade,
    lookup_labor_pension_grade,
)

router = APIRouter(prefix="/employee-changes")
_jinja_env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)


def _safe_format_number(value):
    """安全格式化數字，處理非數值類型"""
    try:
        return "{:,}".format(int(value))
    except (ValueError, TypeError):
        return str(value) if value else ""


_jinja_env.filters["safe_number"] = _safe_format_number

CHANGE_TYPES = [
    ("insurance", "勞健保"),
    ("salary", "薪資"),
    ("tax", "所得稅"),
    ("dependent", "眷屬"),
]

INSURANCE_FIELDS = [
    ("insurance_labor_amount", "勞保投保金額"),
    ("insurance_occupational_amount", "職災保險投保金額"),
    ("insurance_labor_pension_amount", "勞退投保金額"),
    ("labor_pension_employer_rate", "勞退雇主提繳率(%)"),
    ("labor_pension_personal_rate", "勞退個人提繳率(%)"),
    ("insurance_health_amount", "健保投保金額"),
    ("has_exemption", "減免身分"),
    ("subsidy_rate", "補助費率(%)"),
]

SALARY_FIELDS = [
    ("hourly_wage", "時薪"),
]

TAX_FIELDS = [
    ("tax_dependents", "所得稅扶養人數"),
]

DEPENDENT_FIELDS = [
    ("name", "姓名"),
    ("dep_relationship", "稱謂"),
    ("birth_date", "出生日期"),
    ("nationality", "籍別"),
    ("has_exemption", "減免身分"),
    ("subsidy_rate", "補助費率(%)"),
    ("max_subsidy_amount", "最高補助金額"),
    ("enrollment_date", "加保日期"),
    ("termination_date", "退保日期"),
]

FIELDS_BY_TYPE = {
    "insurance": INSURANCE_FIELDS,
    "salary": SALARY_FIELDS,
    "tax": TAX_FIELDS,
    "dependent": DEPENDENT_FIELDS,
}

SOURCES = [
    ("apollo_import", "Apollo 匯入"),
    ("manual", "手動"),
    ("annual_adjustment", "年度調整"),
]


def _get_current_values(user: User, db: Session) -> dict:
    """取得員工目前各欄位的生效值。"""
    today = date.today()
    values = {}
    for field_list in FIELDS_BY_TYPE.values():
        for field_name, _ in field_list:
            val = user.get_change_value(db, field_name, as_of=today)
            if val is not None:
                values[field_name] = val
            else:
                user_val = getattr(user, field_name, None)
                if user_val is not None:
                    values[field_name] = user_val
    return values


def _get_insurance_calc(cv: dict, dependents: list = None) -> dict:
    """根據目前值計算各保險自付額。"""
    labor_grade = cv.get("insurance_labor_amount", 0)
    health_grade = cv.get("insurance_health_amount", 0)
    pension_grade = cv.get("insurance_labor_pension_amount", 0)
    pension_self_rate = cv.get("labor_pension_personal_rate", 0)

    # 計算眷屬人數（僅 active）
    active_deps = [d for d in (dependents or []) if d.is_active]

    li = calc_labor_insurance_self_pay(labor_grade)
    hi_emp = calc_health_insurance_self_pay(health_grade)
    hi_dep = calc_health_insurance_dependent_pay(health_grade, active_deps)
    hi = round(hi_emp + hi_dep)
    lp = calc_labor_pension_self_pay(pension_grade, pension_self_rate)

    return {
        "labor_insurance_self": li,
        "health_insurance_self": hi,
        "health_subsidy": 0,
        "labor_pension_self": lp,
        "total_deduction": li + hi + lp,
    }


@router.get("", response_class=HTMLResponse)
def employee_changes_page(
    request: Request,
    employee_id: str = "",
    change_type: str = "",
    new_hire: str = "",
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    caregivers = (db.query(User)
                  .filter(User.is_active.is_(True), User.role == UserRole.caregiver)
                  .order_by(User.employee_no).all())

    selected = None
    changes = []
    current_values = {}
    insurance_calc = {}
    dependents = []

    if employee_id:
        selected = db.query(User).filter(User.id == employee_id).first()
    elif caregivers:
        selected = caregivers[0]

    if selected:
        q = (db.query(EmployeeChange)
             .filter(EmployeeChange.employee_id == selected.id))
        if change_type:
            q = q.filter(EmployeeChange.change_type == change_type)
        changes = q.order_by(desc(EmployeeChange.effective_date), desc(EmployeeChange.created_at)).all()
        current_values = _get_current_values(selected, db)

        # Fetch actual dependents from DB
        dependents = (db.query(NhiDependent)
                      .filter(NhiDependent.employee_id == selected.id)
                      .order_by(NhiDependent.enrollment_date.desc())
                      .all())
        insurance_calc = _get_insurance_calc(current_values, dependents)

    # Grade options for dropdowns (value, label)
    labor_grades = [(g, f"{g:,}") for _, g in LABOR_INSURANCE_GRADES]
    health_grades = [(g, f"{g:,}") for _, g in HEALTH_INSURANCE_GRADES]
    pension_grades = [(g, f"{g:,}") for _, g in LABOR_PENSION_GRADES]

    today_str = date.today().isoformat()

    template = _jinja_env.get_template("employee_changes.html")
    try:
        html = template.render(
            user=user,
            caregivers=caregivers,
            selected=selected,
            changes=changes,
            current_values=current_values,
            insurance_calc=insurance_calc,
            dependents=dependents,
            change_types=CHANGE_TYPES,
            fields_by_type={k: [list(i) for i in v] for k, v in FIELDS_BY_TYPE.items()},
            sources=SOURCES,
            selected_type=change_type,
            EmployeeChange=EmployeeChange,
            labor_grades=labor_grades,
            health_grades=health_grades,
            pension_grades=pension_grades,
            today_str=today_str,
            new_hire=new_hire == "1",
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HTMLResponse(content=f"<pre>{traceback.format_exc()}</pre>", status_code=500)
    return HTMLResponse(content=html)


@router.post("/update-insurance")
def update_insurance(
    request: Request,
    employee_id: str = Form(...),
    insurance_labor_amount: int = Form(...),
    insurance_health_amount: int = Form(...),
    insurance_labor_pension_amount: int = Form(...),
    labor_pension_personal_rate: int = Form(0),
    has_exemption: bool = Form(False),
    subsidy_rate: int = Form(0),
    insurance_note: str = Form(""),
    effective_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == employee_id).first()
    if not target:
        return RedirectResponse(url="/employee-changes", status_code=302)

    eff = date.fromisoformat(effective_date)

    # 先處理直接存在 User 上的欄位（不經過異動紀錄）
    target.has_exemption = has_exemption
    target.subsidy_rate = subsidy_rate
    target.insurance_note = insurance_note

    # 保險級距走異動紀錄
    grade_fields = {
        "insurance_labor_amount": insurance_labor_amount,
        "insurance_health_amount": insurance_health_amount,
        "insurance_labor_pension_amount": insurance_labor_pension_amount,
        "labor_pension_personal_rate": labor_pension_personal_rate,
    }

    for field_name, new_val in grade_fields.items():
        old_val = target.get_change_value(db, field_name) or 0
        if old_val != new_val:
            change = EmployeeChange(
                employee_id=employee_id,
                change_type="insurance",
                field_name=field_name,
                effective_date=eff,
                old_value=old_val,
                new_value=new_val,
                source="manual",
                created_by=user.id,
            )
            db.add(change)
    db.commit()
    return RedirectResponse(
        url=f"/employee-changes?employee_id={employee_id}",
        status_code=302,
    )


@router.post("/add")
def add_change(
    employee_id: str = Form(...),
    change_type: str = Form(...),
    field_name: str = Form(...),
    effective_date: str = Form(...),
    new_value: int = Form(...),
    source: str = Form("manual"),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == employee_id).first()
    if not target:
        return RedirectResponse(url="/employee-changes", status_code=302)

    old_value = target.get_change_value(db, field_name) or 0

    change = EmployeeChange(
        employee_id=employee_id,
        change_type=change_type,
        field_name=field_name,
        effective_date=date.fromisoformat(effective_date),
        old_value=old_value,
        new_value=new_value,
        source=source,
        created_by=user.id,
    )
    db.add(change)
    db.commit()
    return RedirectResponse(
        url=f"/employee-changes?employee_id={employee_id}&change_type={change_type}",
        status_code=302,
    )


@router.post("/delete/{change_id}")
def delete_change(
    change_id: str,
    user: User = Depends(require_roles("主管", "主任")),
    db: Session = Depends(get_db),
):
    change = db.query(EmployeeChange).filter(EmployeeChange.id == change_id).first()
    if change:
        emp_id = change.employee_id
        ct = change.change_type
        db.delete(change)
        db.commit()
        return RedirectResponse(
            url=f"/employee-changes?employee_id={emp_id}&change_type={ct}",
            status_code=302,
        )
    return RedirectResponse(url="/employee-changes", status_code=302)


@router.post("/terminate-insurance")
def terminate_insurance(
    employee_id: str = Form(...),
    termination_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    """退保：設定離職生效日，退保日 = 離職生效日 - 1 日"""
    target = db.query(User).filter(User.id == employee_id).first()
    if not target:
        return RedirectResponse(url="/employee-changes", status_code=302)

    term_date = date.fromisoformat(termination_date)
    insurance_term_date = term_date - __import__('datetime').timedelta(days=1)

    # 記錄異動
    fields_to_terminate = {
        "insurance_labor_amount": 0,
        "insurance_health_amount": 0,
        "insurance_labor_pension_amount": 0,
        "labor_pension_personal_rate": 0,
    }
    for field_name, new_val in fields_to_terminate.items():
        old_val = target.get_change_value(db, field_name) or 0
        if old_val != new_val:
            change = EmployeeChange(
                employee_id=employee_id,
                change_type="insurance",
                field_name=field_name,
                effective_date=insurance_term_date,
                old_value=old_val,
                new_value=new_val,
                source="manual",
                created_by=user.id,
            )
            db.add(change)

    # 設定離職日期
    target.termination_date = term_date

    # 退保所有眷屬
    from app.models.nhi_dependent import NhiDependent
    active_deps = db.query(NhiDependent).filter(
        NhiDependent.employee_id == employee_id,
        NhiDependent.is_active.is_(True),
    ).all()
    for dep in active_deps:
        dep.is_active = False
        dep.termination_date = insurance_term_date

    db.commit()
    return RedirectResponse(
        url=f"/employee-changes?employee_id={employee_id}",
        status_code=302,
    )


@router.post("/reactivate-insurance")
def reactivate_insurance(
    employee_id: str = Form(...),
    effective_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    """復保：取消離職日期，重新投保"""
    target = db.query(User).filter(User.id == employee_id).first()
    if not target:
        return RedirectResponse(url="/employee-changes", status_code=302)

    eff = date.fromisoformat(effective_date)

    # 記錄異動（復保需要重新設定級距）
    target.termination_date = None

    db.commit()
    return RedirectResponse(
        url=f"/employee-changes?employee_id={employee_id}",
        status_code=302,
    )


@router.post("/init-insurance")
def init_insurance(
    employee_id: str = Form(...),
    hire_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    """新進人員：自動建立保險異動紀錄，投保日=到職日"""
    target = db.query(User).filter(User.id == employee_id).first()
    if not target:
        return RedirectResponse(url="/employee-changes", status_code=302)

    eff = date.fromisoformat(hire_date)

    # 自動建立保險異動紀錄
    default_fields = {
        "insurance_labor_amount": 0,
        "insurance_health_amount": 0,
        "insurance_labor_pension_amount": 0,
        "labor_pension_personal_rate": 0,
    }
    for field_name, new_val in default_fields.items():
        change = EmployeeChange(
            employee_id=employee_id,
            change_type="insurance",
            field_name=field_name,
            effective_date=eff,
            old_value=0,
            new_value=new_val,
            source="manual",
            created_by=user.id,
        )
        db.add(change)

    db.commit()
    return RedirectResponse(
        url=f"/employee-changes?employee_id={employee_id}&new_hire=1",
        status_code=302,
    )


@router.post("/add-dependent")
def add_dependent(
    employee_id: str = Form(...),
    dep_name: str = Form(...),
    dep_id_number: str = Form(""),
    dep_nationality: str = Form("本國人"),
    dep_relationship: str = Form(...),
    dep_birth_date: str = Form(""),
    dep_has_exemption: bool = Form(False),
    dep_subsidy_rate: int = Form(0),
    dep_max_subsidy_amount: int = Form(0),
    dep_enrollment_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    """新增眷屬並記錄異動"""
    target = db.query(User).filter(User.id == employee_id).first()
    if not target:
        return RedirectResponse(url="/employee-changes", status_code=302)

    # 建立眷屬紀錄
    dep = NhiDependent(
        employee_id=employee_id,
        name=dep_name,
        id_number=dep_id_number,
        nationality=dep_nationality,
        dep_relationship=dep_relationship,
        birth_date=date.fromisoformat(dep_birth_date) if dep_birth_date else None,
        has_exemption=dep_has_exemption,
        subsidy_rate=dep_subsidy_rate,
        max_subsidy_amount=dep_max_subsidy_amount,
        enrollment_date=date.fromisoformat(dep_enrollment_date),
        is_active=True,
    )
    db.add(dep)
    db.flush()  # 取得 dep.id

    # 記錄異動（顯示在異動紀錄中）
    # new_value = 1 表示新增眷屬（依賴眷屬人數從 DB 自動計算）
    change = EmployeeChange(
        employee_id=employee_id,
        change_type="dependent",
        field_name="name",
        effective_date=date.fromisoformat(dep_enrollment_date),
        old_value=0,
        new_value=1,
        source="manual",
        created_by=user.id,
    )
    db.add(change)
    db.commit()

    return RedirectResponse(
        url=f"/employee-changes?employee_id={employee_id}&change_type=dependent",
        status_code=302,
    )


@router.post("/terminate-dependent/{dep_id}")
def terminate_dependent(
    dep_id: str,
    termination_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    """退保眷屬"""
    dep = db.query(NhiDependent).filter(NhiDependent.id == dep_id).first()
    if dep:
        emp_id = dep.employee_id
        dep.is_active = False
        dep.termination_date = date.fromisoformat(termination_date)

        # 記錄異動
        # old_value = 1 表示退保前有眷屬（依賴眷屬人數從 DB 自動計算）
        change = EmployeeChange(
            employee_id=emp_id,
            change_type="dependent",
            field_name="name",
            effective_date=date.fromisoformat(termination_date),
            old_value=1,
            new_value=0,
            source="manual",
            created_by=user.id,
        )
        db.add(change)
        db.commit()

        return RedirectResponse(
            url=f"/employee-changes?employee_id={emp_id}",
            status_code=302,
        )
