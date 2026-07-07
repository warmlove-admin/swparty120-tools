from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.database import get_db
from app.models.care_plan import CarePlan
from app.models.case import Case
from app.models.assessment import Assessment
from app.models.goal import Goal, GoalStatus
from app.models.service_schedule import ServiceSchedule
from app.models.user import User, UserRole
from app.services.ltc_code_catalog import BA_CODES, GA_SC_CODES, ALL_CODES, CODE_LOOKUP
from app.services.schedule_formalization import sync_case_external_schedule_status

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
    """從勾選的checkbox + 數量 + 經費來源輸入，組成coded_services清單"""
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
                "funding_source": form.get(f"funding_{code}", "補助"),
            })
    return coded_services


def _selectable_goals_query(db: Session, case_id: str, assessment: Assessment | None = None):
    query = db.query(Goal).filter(Goal.case_id == case_id)
    if assessment:
        query = query.filter(
            Goal.origin_assessment_id == assessment.id,
            Goal.status == GoalStatus.in_progress,
        )
    return query.order_by(Goal.set_date.desc())


@router.get("/new", response_class=HTMLResponse)
def new_care_plan_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    goal_id: str = "",
    assessment_id: str = "",
):
    case = _get_case_or_404(db, case_id)
    caregivers = db.query(User).filter(User.role == UserRole.caregiver, User.is_active.is_(True)).all()
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first() if assessment_id else None
    if not assessment and goal_id:
        source_goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
        assessment = source_goal.origin_assessment if source_goal else None
    selectable_goals = _selectable_goals_query(db, case_id, assessment).all()
    return templates.TemplateResponse(
        request, "care_plan_form.html",
        {
            "case": case, "user": user, "caregivers": caregivers,
            "ba_codes": BA_CODES, "ga_sc_codes": GA_SC_CODES,
            "preselect_goal_id": goal_id,
            "care_plan": None,
            "origin_assessment": assessment,
            "selectable_goals": selectable_goals,
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
    origin_assessment_id = form.get("origin_assessment_id") or ""
    origin_assessment = db.query(Assessment).filter(Assessment.id == origin_assessment_id, Assessment.case_id == case.id).first() if origin_assessment_id else None
    if not origin_assessment and goal_ids:
        source_goal = db.query(Goal).filter(Goal.id == goal_ids[0], Goal.case_id == case.id).first()
        origin_assessment = source_goal.origin_assessment if source_goal else None

    selected_goals = _selectable_goals_query(db, case.id, origin_assessment).filter(Goal.id.in_(goal_ids)).all() if goal_ids else []
    if not selected_goals:
        raise HTTPException(400, "請至少選擇一個可用的進行中照顧目標。")

    care_plan = CarePlan(
        case_id=case.id,
        coded_services=_build_coded_services(form),
        assigned_caregiver_id=form.get("assigned_caregiver_id") or None,
        note=form.get("note") or None,
        origin_assessment_id=origin_assessment.id if origin_assessment else None,
    )
    care_plan.goals = selected_goals
    db.add(care_plan)
    db.flush()
    sync_case_external_schedule_status(db, case.id)
    db.commit()

    redirect_goal_id = goal_ids[0] if goal_ids else None
    if redirect_goal_id:
        return RedirectResponse(url=f"/cases/{case.id}/goals/{redirect_goal_id}", status_code=302)
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.post("/{care_plan_id}/delete")
def delete_care_plan(
    case_id: str,
    care_plan_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    care_plan = _get_care_plan_or_404(db, case_id, care_plan_id)
    has_successor = db.query(CarePlan).filter(CarePlan.predecessor_care_plan_id == care_plan.id).first()
    if has_successor:
        raise HTTPException(400, "此照顧計畫已有後續承接計畫，不能直接刪除；請改以後續計畫調整服務。")
    # 班表是由照顧計畫的服務項目建立；誤刪計畫時一併移除，避免留下無來源班表。
    db.query(ServiceSchedule).filter(ServiceSchedule.care_plan_id == care_plan.id).delete()
    db.delete(care_plan)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}?tab=care", status_code=302)


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
    linked_goal_ids = {goal.id for goal in care_plan.goals}
    selectable_goals = _selectable_goals_query(db, case_id, care_plan.origin_assessment).all()
    if linked_goal_ids:
        linked_goals = db.query(Goal).filter(Goal.id.in_(linked_goal_ids), Goal.case_id == case_id).all()
        selectable_by_id = {goal.id: goal for goal in selectable_goals}
        for goal in linked_goals:
            selectable_by_id.setdefault(goal.id, goal)
        selectable_goals = list(selectable_by_id.values())
    return templates.TemplateResponse(
        request, "care_plan_form.html",
        {
            "case": case, "user": user, "caregivers": caregivers,
            "ba_codes": BA_CODES, "ga_sc_codes": GA_SC_CODES,
            "preselect_goal_id": "",
            "care_plan": care_plan,
            "origin_assessment": care_plan.origin_assessment,
            "selectable_goals": selectable_goals,
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
    if goal_ids:
        allowed_goals = _selectable_goals_query(db, case.id, care_plan.origin_assessment).filter(Goal.id.in_(goal_ids)).all()
        current_linked = db.query(Goal).filter(
            Goal.id.in_(goal_ids),
            Goal.case_id == case.id,
            Goal.care_plans.any(CarePlan.id == care_plan.id),
        ).all()
        allowed_by_id = {goal.id: goal for goal in allowed_goals}
        for goal in current_linked:
            allowed_by_id.setdefault(goal.id, goal)
        care_plan.goals = list(allowed_by_id.values())
        if not care_plan.goals:
            raise HTTPException(400, "請至少選擇一個可用的進行中照顧目標。")
    else:
        raise HTTPException(400, "請至少選擇一個可用的進行中照顧目標。")
    sync_case_external_schedule_status(db, case.id)
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)
