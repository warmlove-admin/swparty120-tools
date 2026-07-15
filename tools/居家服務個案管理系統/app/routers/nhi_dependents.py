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
    name: str = Form(...),
    id_number: str = Form(""),
    relationship: str = Form(...),
    birth_date: str = Form(""),
    enrollment_date: str = Form(...),
    user: User = Depends(require_roles("居督", "主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    dep = NhiDependent(
        employee_id=employee_id,
        name=name,
        id_number=id_number,
        dep_relationship=relationship,
        birth_date=date.fromisoformat(birth_date) if birth_date else None,
        enrollment_date=date.fromisoformat(enrollment_date),
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
