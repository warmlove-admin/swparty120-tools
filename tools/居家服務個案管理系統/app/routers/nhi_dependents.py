from datetime import date
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.database import get_db
from app.models.user import User
from app.models.nhi_dependent import NhiDependent

router = APIRouter(prefix="/nhi-dependents")


@router.post("/add")
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
    db.commit()
    return RedirectResponse(
        url=f"/employee-changes?employee_id={employee_id}",
        status_code=302,
    )


@router.post("/delete/{dep_id}")
def delete_dependent(
    dep_id: str,
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    dep = db.query(NhiDependent).filter(NhiDependent.id == dep_id).first()
    if dep:
        emp_id = dep.employee_id
        db.delete(dep)
        db.commit()
        return RedirectResponse(
            url=f"/employee-changes?employee_id={emp_id}",
            status_code=302,
        )
    return RedirectResponse(url="/employee-changes", status_code=302)


@router.post("/toggle/{dep_id}")
def toggle_dependent(
    dep_id: str,
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    dep = db.query(NhiDependent).filter(NhiDependent.id == dep_id).first()
    if dep:
        dep.is_active = not dep.is_active
        if not dep.is_active:
            dep.termination_date = date.today()
        else:
            dep.termination_date = None
        db.commit()
        return RedirectResponse(
            url=f"/employee-changes?employee_id={dep.employee_id}",
            status_code=302,
        )
    return RedirectResponse(url="/employee-changes", status_code=302)
