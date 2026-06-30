from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.care_plan import CarePlan
from app.models.case import Case
from app.models.assessment import Assessment
from app.models.goal import Goal, GoalProgressLog, GoalStatus, GoalTemplate
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


def _linkable_care_plans(db: Session, case_id: str, goal: Goal) -> list[CarePlan]:
    if goal.status != GoalStatus.in_progress:
        return []
    query = db.query(CarePlan).filter(CarePlan.case_id == case_id)
    if goal.origin_assessment_id:
        query = query.filter(CarePlan.origin_assessment_id == goal.origin_assessment_id)
    else:
        query = query.filter(CarePlan.origin_assessment_id.is_(None))
    return [care_plan for care_plan in query.all() if goal not in care_plan.goals]


@router.get("/new", response_class=HTMLResponse)
def new_goal_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    assessment_id: str = "",
):
    case = _get_case_or_404(db, case_id)
    templates_by_domain = db.query(GoalTemplate).order_by(GoalTemplate.domain).all()
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first() if assessment_id else None
    if not assessment:
        raise HTTPException(400, "請從指定評估紀錄補登照顧目標。")
    return templates.TemplateResponse(
        request, "goal_form.html",
        {"case": case, "user": user, "domains": DOMAINS, "goal_templates": templates_by_domain, "origin_assessment": assessment},
    )


@router.post("")
def create_goal(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    domain: str = Form(...),
    description: str = Form(...),
    template_id: str = Form(""),
    origin_assessment_id: str = Form(""),
):
    case = _get_case_or_404(db, case_id)
    origin_assessment = db.query(Assessment).filter(Assessment.id == origin_assessment_id, Assessment.case_id == case.id).first() if origin_assessment_id else None
    if not origin_assessment:
        raise HTTPException(400, "請先完成評估，再依該次評估建立照顧目標。")
    goal = Goal(
        case_id=case.id,
        domain=domain,
        description=description,
        template_id=template_id or None,
        origin_assessment_id=origin_assessment.id if origin_assessment else None,
        set_date=origin_assessment.assessment_date,
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
    linkable_plans = _linkable_care_plans(db, case_id, goal)
    has_successor = db.query(Goal).filter(Goal.predecessor_goal_id == goal.id).first() is not None
    protected_plan_ids = {
        plan.predecessor_care_plan_id
        for plan in case.care_plans
        if plan.predecessor_care_plan_id
    }
    progress_logs = db.query(GoalProgressLog).filter(GoalProgressLog.goal_id == goal.id).order_by(GoalProgressLog.judged_at.desc()).all()
    return templates.TemplateResponse(
        request, "goal_detail.html",
        {
            "case": case,
            "goal": goal,
            "user": user,
            "linkable_plans": linkable_plans,
            "progress_logs": progress_logs,
            "can_delete_goal": not has_successor,
            "protected_plan_ids": protected_plan_ids,
        },
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
    status: str = Form(...),
):
    case = _get_case_or_404(db, case_id)
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
    if not goal:
        raise HTTPException(404, "照顧目標不存在")
    goal.domain = domain
    goal.description = description
    goal.status = GoalStatus[status]
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}/goals/{goal.id}", status_code=302)


@router.post("/{goal_id}/delete")
def delete_goal(
    case_id: str,
    goal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
    if not goal:
        raise HTTPException(404, "照顧目標不存在")
    has_successor = db.query(Goal).filter(Goal.predecessor_goal_id == goal.id).first()
    if has_successor:
        raise HTTPException(400, "此目標已有後續承接目標，不能直接刪除；請改以狀態標示停用或更換。")
    # 保留照顧計畫本身，但解除關聯並刪除該目標的歷次檢討，以維持資料完整性。
    goal.care_plans.clear()
    db.query(GoalProgressLog).filter(GoalProgressLog.goal_id == goal.id).delete()
    db.delete(goal)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}?tab=care", status_code=302)


@router.post("/{goal_id}/link_care_plan")
def link_care_plan(
    case_id: str,
    goal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
    care_plan_id: str = Form(...),
):
    case = _get_case_or_404(db, case_id)
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.case_id == case_id).first()
    care_plan = db.query(CarePlan).filter(CarePlan.id == care_plan_id, CarePlan.case_id == case_id).first()
    if not goal or not care_plan:
        raise HTTPException(404, "目標或照顧計畫不存在")
    if goal.status != GoalStatus.in_progress:
        raise HTTPException(400, "只有進行中的目標可以連結照顧計畫")
    if goal.origin_assessment_id != care_plan.origin_assessment_id:
        raise HTTPException(400, "只能連結同一評估來源的照顧計畫")
    if goal not in care_plan.goals:
        care_plan.goals.append(goal)
        db.commit()
    return RedirectResponse(url=f"/cases/{case.id}/goals/{goal.id}", status_code=302)
