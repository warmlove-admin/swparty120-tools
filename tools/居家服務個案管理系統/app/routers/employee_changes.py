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
    calc_health_insurance_subsidy,
    calc_labor_pension_self_pay,
    calc_total_employee_deduction,
    lookup_labor_insurance_grade,
    lookup_health_insurance_grade,
    lookup_labor_pension_grade,
)

router = APIRouter(prefix="/employee-changes")
_jinja_env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)

CHANGE_TYPES = [
    ("insurance", "勞健保"),
    ("salary", "薪資"),
    ("tax", "所得稅"),
]

INSURANCE_FIELDS = [
    ("insurance_labor_amount", "勞保投保金額"),
    ("insurance_occupational_amount", "職災保險投保金額"),
    ("insurance_labor_pension_amount", "勞退投保金額"),
    ("labor_pension_employer_rate", "勞退雇主提繳率(%)"),
    ("labor_pension_personal_rate", "勞退個人提繳率(%)"),
    ("insurance_health_amount", "健保投保金額"),
    ("health_dependents", "健保眷屬人數"),
    ("has_exemption", "減免身分"),
]

SALARY_FIELDS = [
    ("hourly_wage", "時薪"),
]

TAX_FIELDS = [
    ("tax_dependents", "所得稅扶養人數"),
]

FIELDS_BY_TYPE = {
    "insurance": INSURANCE_FIELDS,
    "salary": SALARY_FIELDS,
    "tax": TAX_FIELDS,
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
    dep_count = len(active_deps)

    li = calc_labor_insurance_self_pay(labor_grade)
    hi_base = calc_health_insurance_self_pay(health_grade, dep_count)
    hi_subsidy = calc_health_insurance_subsidy(health_grade, active_deps)
    hi = hi_base - hi_subsidy
    lp = calc_labor_pension_self_pay(pension_grade, pension_self_rate)

    return {
        "labor_insurance_self": li,
        "health_insurance_self": hi,
        "health_subsidy": hi_subsidy,
        "labor_pension_self": lp,
        "total_deduction": li + hi + lp,
    }


@router.get("", response_class=HTMLResponse)
def employee_changes_page(
    request: Request,
    employee_id: str = "",
    change_type: str = "",
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
    )
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
    effective_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == employee_id).first()
    if not target:
        return RedirectResponse(url="/employee-changes", status_code=302)

    eff = date.fromisoformat(effective_date)
    fields = {
        "insurance_labor_amount": insurance_labor_amount,
        "insurance_health_amount": insurance_health_amount,
        "insurance_labor_pension_amount": insurance_labor_pension_amount,
        "labor_pension_personal_rate": labor_pension_personal_rate,
        "has_exemption": has_exemption,
    }

    for field_name, new_val in fields.items():
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
