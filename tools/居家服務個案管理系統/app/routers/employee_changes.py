from datetime import date, datetime
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.user import User, UserRole
from app.models.employee_change import EmployeeChange

router = APIRouter(prefix="/employee-changes")
templates = Jinja2Templates(directory="app/templates")

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


@router.get("", response_class=HTMLResponse)
def employee_changes_page(
    request: Request,
    employee_id: str = "",
    change_type: str = "",
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    # All active caregivers
    caregivers = (db.query(User)
                  .filter(User.is_active.is_(True), User.role == UserRole.caregiver)
                  .order_by(User.employee_no).all())

    selected = None
    changes = []
    current_values = {}

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

        # Compute current values as of today
        today = date.today()
        for field_list in FIELDS_BY_TYPE.values():
            for field_name, _ in field_list:
                val = selected.get_change_value(db, field_name, as_of=today)
                if val is not None:
                    current_values[field_name] = val
                else:
                    # Fallback to User table column
                    user_val = getattr(selected, field_name, None)
                    if user_val is not None:
                        current_values[field_name] = user_val

    return templates.TemplateResponse("employee_changes.html", {
        "request": request,
        "user": user,
        "caregivers": caregivers,
        "selected": selected,
        "changes": changes,
        "current_values": current_values,
        "change_types": CHANGE_TYPES,
        "fields_by_type": FIELDS_BY_TYPE,
        "sources": SOURCES,
        "selected_type": change_type,
        "EmployeeChange": EmployeeChange,
    })


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

    # Get old value
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
