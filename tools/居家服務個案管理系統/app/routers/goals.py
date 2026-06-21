from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.case import Case
from app.models.goal import Goal, GoalStatus, GoalTemplate
from app.models.user import User, UserRole

router = APIRouter(prefix="/cases/{case_id}/goals")
templates = Jinja2Templates(directory="app/templates")

DOMAINS = [
    "身體功能面", "認知與心理面", "家庭與照顧者面", "居家環境面",
    "福利資源與服務滿意度", "社交、外出與人際面",
]


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "個案不存在")
    return case


@router.get("/new", response_class=HTMLResponse)
def new_goal_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    templates_by_domain = db.query(GoalTemplate).order_by(GoalTemplate.domain).all()
    return templates.TemplateResponse(
        request, "goal_form.html",
        {"case": case, "user": user, "domains": DOMAINS, "goal_templates": templates_by_domain},
    )


@router.post("")
def create_goal(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    domain: str = Form(...),
    description: str = Form(...),
    template_id: str = Form(""),
    set_date: str = Form(...),
):
    case = _get_case_or_404(db, case_id)
    goal = Goal(
        case_id=case.id,
        domain=domain,
        description=description,
        template_id=template_id or None,
        set_date=date.fromisoformat(set_date),
        status=GoalStatus.in_progress,
    )
    db.add(goal)
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}/goals/{goal.id}", status_code=302)


@router.get("/{goal_id}", response_class=HTMLResponse)
def goal_detail(
    case_id: str,
    goal_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = _get_case_or_404(db, case_id)
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
    if not goal:
        raise HTTPException(404, "照顧目標不存在")
    # 同一個案尚未連結到此目標的照顧計畫，供「連結現有照顧計畫」功能選用
    linkable_plans = [cp for cp in case.care_plans if goal not in cp.goals]
    return templates.TemplateResponse(
        request, "goal_detail.html",
        {"case": case, "goal": goal, "user": user, "linkable_plans": linkable_plans},
    )


@router.get("/{goal_id}/edit", response_class=HTMLResponse)
def edit_goal_form(
    case_id: str,
    goal_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
    if not goal:
        raise HTTPException(404, "照顧目標不存在")
    return templates.TemplateResponse(
        request, "goal_edit_form.html",
        {"case": case, "goal": goal, "user": user, "domains": DOMAINS, "statuses": list(GoalStatus)},
    )


@router.post("/{goal_id}/edit")
def edit_goal(
    case_id: str,
    goal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    domain: str = Form(...),
    description: str = Form(...),
    set_date: str = Form(...),
    status: str = Form(...),
):
    case = _get_case_or_404(db, case_id)
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
    if not goal:
        raise HTTPException(404, "照顧目標不存在")
    goal.domain = domain
    goal.description = description
    goal.set_date = date.fromisoformat(set_date)
    goal.status = GoalStatus[status]
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}/goals/{goal.id}", status_code=302)


@router.post("/{goal_id}/link_care_plan")
def link_care_plan(
    case_id: str,
    goal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    care_plan_id: str = Form(...),
):
    from app.models.care_plan import CarePlan

    case = _get_case_or_404(db, case_id)
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
    care_plan = db.query(CarePlan).filter(CarePlan.id == care_plan_id, CarePlan.case_id == case_id).first()
    if not goal or not care_plan:
        raise HTTPException(404, "目標或照顧計畫不存在")
    if goal not in care_plan.goals:
        care_plan.goals.append(goal)
        db.commit()
    return RedirectResponse(url=f"/cases/{case.id}/goals/{goal.id}", status_code=302)
