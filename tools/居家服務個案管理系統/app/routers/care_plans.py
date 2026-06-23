from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.database import get_db
from app.models.care_plan import CarePlan
from app.models.case import Case
from app.models.goal import Goal
from app.models.user import User, UserRole
from app.services.ltc_code_catalog import BA_CODES, GA_SC_CODES, ALL_CODES, CODE_LOOKUP

router = APIRouter(prefix="/cases/{case_id}/care_plans")
templates = Jinja2Templates(directory="app/templates")


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "個案不存在")
    return case


def _get_care_plan_or_404(db: Session, case_id: str, care_plan_id: str) -> CarePlan:
    care_plan = db.query(CarePlan).filter(CarePlan.id == care_plan_id, CarePlan.case_id == case_id).first()
    if not care_plan:
        raise HTTPException(404, "照顧計畫不存在")
    return care_plan


def _build_coded_services(form) -> list:
    """從勾選的checkbox + 數量輸入，組成coded_services清單"""
    coded_services = []
    for code, name, minutes in ALL_CODES:
        if form.get(f"code_{code}"):
            qty = int(form.get(f"qty_{code}") or 1)
            coded_services.append({
                "code": code,
                "name": name,
                "quantity": qty,
                "minutes_per_unit": minutes,
                "total_minutes": qty * minutes,
            })
    return coded_services


@router.get("/new", response_class=HTMLResponse)
def new_care_plan_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    goal_id: str = "",
):
    case = _get_case_or_404(db, case_id)
    caregivers = db.query(User).filter(User.role == UserRole.caregiver, User.is_active.is_(True)).all()
    return templates.TemplateResponse(
        request, "care_plan_form.html",
        {
            "case": case, "user": user, "caregivers": caregivers,
            "ba_codes": BA_CODES, "ga_sc_codes": GA_SC_CODES,
            "preselect_goal_id": goal_id,
            "care_plan": None,
        },
    )


@router.post("")
async def create_care_plan(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    form = await request.form()
    goal_ids = form.getlist("goal_ids")

    care_plan = CarePlan(
        case_id=case.id,
        coded_services=_build_coded_services(form),
        assigned_caregiver_id=form.get("assigned_caregiver_id") or None,
        note=form.get("note") or None,
    )
    if goal_ids:
        care_plan.goals = db.query(Goal).filter(Goal.id.in_(goal_ids), Goal.case_id == case.id).all()
    db.add(care_plan)
    db.commit()

    redirect_goal_id = goal_ids[0] if goal_ids else None
    if redirect_goal_id:
        return RedirectResponse(url=f"/cases/{case.id}/goals/{redirect_goal_id}", status_code=302)
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.get("/{care_plan_id}/edit", response_class=HTMLResponse)
def edit_care_plan_form(
    case_id: str,
    care_plan_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    care_plan = _get_care_plan_or_404(db, case_id, care_plan_id)
    caregivers = db.query(User).filter(User.role == UserRole.caregiver, User.is_active.is_(True)).all()
    return templates.TemplateResponse(
        request, "care_plan_form.html",
        {
            "case": case, "user": user, "caregivers": caregivers,
            "ba_codes": BA_CODES, "ga_sc_codes": GA_SC_CODES,
            "preselect_goal_id": "",
            "care_plan": care_plan,
        },
    )


@router.post("/{care_plan_id}/edit")
async def edit_care_plan(
    case_id: str,
    care_plan_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    care_plan = _get_care_plan_or_404(db, case_id, care_plan_id)
    form = await request.form()
    goal_ids = form.getlist("goal_ids")

    care_plan.coded_services = _build_coded_services(form)
    care_plan.assigned_caregiver_id = form.get("assigned_caregiver_id") or None
    care_plan.note = form.get("note") or None
    care_plan.goals = db.query(Goal).filter(Goal.id.in_(goal_ids), Goal.case_id == case.id).all() if goal_ids else []
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)
