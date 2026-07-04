import calendar
from datetime import date
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Integer, cast, desc, func, or_
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.case import Case, CaseStatus, PauseReasonType, CloseReasonType
from app.models.care_plan import CarePlan
from app.models.care_plan import CarePlanAssessmentLink
from app.models.caregiver_service_record import CaregiverServiceRecord
from app.models.service_schedule import ServiceSchedule
from app.models.assessment import Assessment, RecordStatus
from app.models.goal import Goal, GoalDecision, GoalProgressLog, GoalStatus
from app.models.record_status_log import RecordStatusLog
from app.models.contact import Contact, ContactRole
from app.models.contact_record import ContactRecord
from app.models.complaint import Complaint
from app.models.user import User, UserRole
from app.services.schedule_formalization import PENDING_FORMALIZATION, STATUS_LABELS
from app.services.ltc_import import parse_ltc_html

router = APIRouter(prefix="/cases")
templates = Jinja2Templates(directory="app/templates")


def visible_cases_query(db: Session, user: User):
    """7.2 權限矩陣：居督/主管可看所有個案；居服員僅能看到自己服務之個案
    （透過 care_plans.assigned_caregiver_id 直接關聯到 case）。"""
    query = db.query(Case)
    if user.role == UserRole.caregiver:
        visible_case_ids = (
            db.query(CarePlan.case_id)
            .filter(CarePlan.assigned_caregiver_id == user.id)
            .distinct()
        )
        query = query.filter(Case.id.in_(visible_case_ids))
    return query


def _continuation_label(goal: Goal) -> str:
    """取得最初來源評估，避免多次沿用後只看得到上一期。"""
    source = goal
    visited = set()
    while source.predecessor_goal and source.id not in visited:
        visited.add(source.id)
        source = source.predecessor_goal
    if source.origin_assessment:
        return f"延續 {source.origin_assessment.assessment_date} {source.origin_assessment.assessment_type.value}目標"
    return "延續既有目標"


def _latest_return_logs(db: Session, assessment_ids: list[str]) -> dict[str, RecordStatusLog]:
    if not assessment_ids:
        return {}
    logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment",
        RecordStatusLog.record_id.in_(assessment_ids),
    ).order_by(RecordStatusLog.created_at.desc()).all()
    latest_by_record = {}
    for log in logs:
        latest_by_record.setdefault(log.record_id, log)
    returned_statuses = {RecordStatus.pending.value, RecordStatus.approved.value}
    return {
        record_id: log for record_id, log in latest_by_record.items()
        if log.to_status == RecordStatus.draft.value and log.from_status in returned_statuses
    }


def _goal_plan_pairs(goals: list[Goal], plans: list[CarePlan]) -> list[dict]:
    pairs = []
    linked_plan_ids = set()
    for goal in goals:
        linked_plans = [plan for plan in plans if goal in plan.goals]
        linked_plan_ids.update(plan.id for plan in linked_plans)
        pairs.append({"goal": goal, "plans": linked_plans})
    unlinked_plans = [plan for plan in plans if plan.id not in linked_plan_ids]
    if unlinked_plans:
        pairs.append({"goal": None, "plans": unlinked_plans})
    return pairs


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _assessment_due_context(case: Case, today: date | None = None) -> dict:
    today = today or date.today()
    latest_assessment = max(case.assessments, key=lambda item: item.assessment_date, default=None)
    baseline_date = latest_assessment.assessment_date if latest_assessment else case.open_date
    if not baseline_date:
        return {
            "latest_assessment": latest_assessment,
            "baseline_date": None,
            "next_3m": None,
            "next_6m": None,
            "status": "unknown",
            "label": "尚無評估基準日",
        }
    next_3m = _add_months(baseline_date, 3)
    next_6m = _add_months(baseline_date, 6)
    nearest_due = next_3m
    days_until = (nearest_due - today).days
    if days_until < 0:
        status = "overdue"
        label = f"3個月複評逾期 {abs(days_until)} 天"
    elif days_until <= 30:
        status = "soon"
        label = f"3個月複評 {days_until} 天內到期"
    else:
        status = "ok"
        label = f"3個月複評尚餘 {days_until} 天"
    return {
        "latest_assessment": latest_assessment,
        "baseline_date": baseline_date,
        "next_3m": next_3m,
        "next_6m": next_6m,
        "status": status,
        "label": label,
    }


def _display_org_case_no(case: Case) -> str:
    if case.org_case_no.startswith("XLSROW"):
        return "-"
    return case.org_case_no


def _case_sort_key(case: Case) -> tuple[int, int, str]:
    org_case_no = case.org_case_no or ""
    if org_case_no.startswith("XLSROW"):
        return (1, 0, case.name)
    if org_case_no.isdigit():
        return (0, -int(org_case_no), case.name)
    return (1, 0, case.name)


def _recent_caregivers_by_case(db: Session, cases: list[Case], today: date | None = None) -> dict[str, list[str]]:
    if not cases:
        return {}
    today = today or date.today()
    cutoff = _add_months(today, -6)
    case_ids = [case.id for case in cases]
    schedules = (
        db.query(ServiceSchedule)
        .filter(
            ServiceSchedule.case_id.in_(case_ids),
            ServiceSchedule.effective_from <= today,
            or_(ServiceSchedule.effective_until.is_(None), ServiceSchedule.effective_until >= cutoff),
        )
        .all()
    )
    names_by_case: dict[str, set[str]] = {case.id: set() for case in cases}
    for schedule in schedules:
        if schedule.caregiver:
            names_by_case.setdefault(schedule.case_id, set()).add(schedule.caregiver.display_name)
    records = (
        db.query(CaregiverServiceRecord)
        .filter(
            CaregiverServiceRecord.case_id.in_(case_ids),
            CaregiverServiceRecord.service_date >= cutoff,
        )
        .all()
    )
    for record in records:
        if record.caregiver:
            names_by_case.setdefault(record.case_id, set()).add(record.caregiver.display_name)
    return {
        case_id: sorted(names)
        for case_id, names in names_by_case.items()
    }


@router.get("", response_class=HTMLResponse)
def list_cases(
    request: Request,
    status_filter: str = "active",
    supervisor_id: str = "",
    caregiver_id: str = "",
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    valid_filters = {"active": CaseStatus.active, "paused": CaseStatus.paused, "closed": CaseStatus.closed, "all": None}
    if status_filter not in valid_filters:
        status_filter = "active"
    query = visible_cases_query(db, user)
    if valid_filters[status_filter] is not None:
        query = query.filter(Case.status == valid_filters[status_filter])
    if supervisor_id:
        query = query.filter(Case.primary_supervisor_id == supervisor_id)
    q = q.strip()
    if q:
        like_q = f"%{q}%"
        query = query.filter(or_(
            Case.name.ilike(like_q),
            Case.org_case_no.ilike(like_q),
            Case.id_number.ilike(like_q),
            Case.phone.ilike(like_q),
        ))
    if caregiver_id:
        record_case_ids = (
            db.query(CaregiverServiceRecord.case_id)
            .filter(CaregiverServiceRecord.caregiver_id == caregiver_id)
            .distinct()
        )
        schedule_case_ids = (
            db.query(ServiceSchedule.case_id)
            .filter(ServiceSchedule.caregiver_id == caregiver_id)
            .distinct()
        )
        care_plan_case_ids = (
            db.query(CarePlan.case_id)
            .filter(CarePlan.assigned_caregiver_id == caregiver_id)
            .distinct()
        )
        query = query.filter(or_(
            Case.id.in_(record_case_ids),
            Case.id.in_(schedule_case_ids),
            Case.id.in_(care_plan_case_ids),
        ))
    cases = sorted(query.all(), key=_case_sort_key)
    assessment_due_by_case = {case.id: _assessment_due_context(case) for case in cases}
    recent_caregivers_by_case = _recent_caregivers_by_case(db, cases)
    supervisors = db.query(User).filter(User.role.in_([UserRole.supervisor, UserRole.manager])).order_by(User.display_name).all()
    employee_no_number = cast(func.substr(User.employee_no, 2), Integer)
    caregivers = (
        db.query(User)
        .filter(User.role == UserRole.caregiver, User.is_active.is_(True))
        .order_by(User.employee_no.is_(None), desc(employee_no_number), User.display_name)
        .all()
    )
    query_params_without_status = [
        (key, value)
        for key, value in [("supervisor_id", supervisor_id), ("caregiver_id", caregiver_id), ("q", q)]
        if value
    ]
    status_query_suffix = "".join(f"&{key}={quote_plus(str(value))}" for key, value in query_params_without_status)
    returned_assessments = []
    if user.role in {UserRole.supervisor, UserRole.manager, UserRole.director} and cases:
        visible_case_ids = [case.id for case in cases]
        returned_query = db.query(Assessment).join(Case).filter(
            Assessment.case_id.in_(visible_case_ids),
            Assessment.status == RecordStatus.draft,
        )
        if user.role == UserRole.supervisor:
            returned_query = returned_query.filter(
                or_(Assessment.assessor_id == user.id, Case.primary_supervisor_id == user.id)
            )
        draft_assessments = returned_query.order_by(Assessment.updated_at.desc()).all()
        return_logs = _latest_return_logs(db, [assessment.id for assessment in draft_assessments])
        returned_assessments = [
            {"assessment": assessment, "return_log": return_logs[assessment.id]}
            for assessment in draft_assessments
            if assessment.id in return_logs
        ]
    return templates.TemplateResponse(
        request,
        "cases_list.html",
        {
            "cases": cases,
            "user": user,
            "status_filter": status_filter,
            "supervisor_id": supervisor_id,
            "caregiver_id": caregiver_id,
            "q": q,
            "supervisors": supervisors,
            "caregivers": caregivers,
            "status_query_suffix": status_query_suffix,
            "display_org_case_no": _display_org_case_no,
            "recent_caregivers_by_case": recent_caregivers_by_case,
            "returned_assessments": returned_assessments,
            "assessment_due_by_case": assessment_due_by_case,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_case_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    supervisors = db.query(User).filter(User.role.in_([UserRole.supervisor, UserRole.manager])).all()
    return templates.TemplateResponse(
        request, "case_form.html", {"user": user, "supervisors": supervisors, "error": None, "prefill": None}
    )


@router.post("/import", response_class=HTMLResponse)
async def import_ltc_html(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    file: UploadFile = File(...),
):
    """5.4 步驟1-4：居督上傳衛福部HTML匯出檔，系統解析後預填開案表單，
    步驟5居督檢查無誤後仍可修改，再送出建立個案。"""
    supervisors = db.query(User).filter(User.role.in_([UserRole.supervisor, UserRole.manager])).all()
    content = await file.read()
    prefill = parse_ltc_html(content)
    return templates.TemplateResponse(
        request,
        "case_form.html",
        {"user": user, "supervisors": supervisors, "error": None, "prefill": prefill},
    )


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


@router.post("")
def create_case(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    org_case_no: str = Form(...),
    name: str = Form(...),
    id_number: str = Form(...),
    birth_date: str = Form(""),
    gender: str = Form(""),
    phone: str = Form(""),
    ltc_welfare_status: str = Form(""),
    disability_category: str = Form(""),
    cms_level: str = Form(""),
    living_status: str = Form(""),
    residence_district: str = Form(""),
    household_address: str = Form(""),
    residence_address: str = Form(""),
    a_unit_name: str = Form(""),
    case_manager_name: str = Form(""),
    case_manager_contact: str = Form(""),
    last_cms_assessment_date: str = Form(""),
    primary_supervisor_id: str = Form(""),
    open_date: str = Form(""),
    line_group_id: str = Form(""),
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
    contact_relation: str = Form(""),
    caregiver_name: str = Form(""),
    caregiver_id_number: str = Form(""),
    caregiver_birth_date: str = Form(""),
    caregiver_relation: str = Form(""),
    caregiver_phone: str = Form(""),
):
    supervisors = db.query(User).filter(User.role.in_([UserRole.supervisor, UserRole.manager])).all()

    if db.query(Case).filter(Case.org_case_no == org_case_no).first():
        return templates.TemplateResponse(
            request,
            "case_form.html",
            {"user": user, "supervisors": supervisors, "error": f"機構案號「{org_case_no}」已存在", "prefill": None},
            status_code=400,
        )

    existing_open_case = (
        db.query(Case)
        .filter(Case.id_number == id_number, Case.status != CaseStatus.closed)
        .first()
    )
    if existing_open_case:
        return templates.TemplateResponse(
            request,
            "case_form.html",
            {
                "user": user,
                "supervisors": supervisors,
                "error": f"此身分證字號已有一筆{existing_open_case.status.value}的個案（{existing_open_case.org_case_no}），請至該個案查看，結案後才能重新開案",
                "prefill": None,
            },
            status_code=400,
        )

    case = Case(
        org_case_no=org_case_no,
        name=name,
        id_number=id_number,
        birth_date=_parse_date(birth_date),
        gender=gender or None,
        phone=phone or None,
        ltc_welfare_status=ltc_welfare_status or None,
        disability_category=disability_category or None,
        cms_level=cms_level or None,
        living_status=living_status or None,
        residence_district=residence_district or None,
        household_address=household_address or None,
        residence_address=residence_address or None,
        a_unit_name=a_unit_name or None,
        case_manager_name=case_manager_name or None,
        case_manager_contact=case_manager_contact or None,
        last_cms_assessment_date=_parse_date(last_cms_assessment_date),
        primary_supervisor_id=primary_supervisor_id or None,
        open_date=_parse_date(open_date),
        line_group_id=line_group_id or None,
        status=CaseStatus.active,
    )
    db.add(case)
    db.flush()  # 取得case.id供下方聯絡人關聯使用

    if contact_name:
        db.add(Contact(
            case_id=case.id,
            contact_role=ContactRole.primary_contact,
            name=contact_name,
            phone=contact_phone or None,
            relation=contact_relation or None,
        ))
    if caregiver_name:
        db.add(Contact(
            case_id=case.id,
            contact_role=ContactRole.primary_caregiver,
            name=caregiver_name,
            id_number=caregiver_id_number or None,
            birth_date=_parse_date(caregiver_birth_date),
            relation=caregiver_relation or None,
            phone=caregiver_phone or None,
        ))

    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.get("/{case_id}", response_class=HTMLResponse)
def case_detail(
    case_id: str,
    request: Request,
    tab: str = "basic",
    schedule_warning: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    if tab not in {"basic", "assessment", "care", "contact", "schedule"}:
        tab = "basic"
    ordered_assessments = db.query(Assessment).filter(Assessment.case_id == case_id).order_by(Assessment.assessment_date).all()
    return_logs = _latest_return_logs(db, [assessment.id for assessment in ordered_assessments])
    assessment_trends = []
    previous = None
    for assessment in ordered_assessments:
        items_by_code = {item.item_code: item for item in assessment.items}
        burden_item = items_by_code.get("FAM_burden_level")
        env_risk_item = items_by_code.get("ENV_risk_level")
        assessment_trends.append({
            "assessment": assessment,
            "adl_change": assessment.adl_total_score - previous.adl_total_score if previous and assessment.adl_total_score is not None and previous.adl_total_score is not None else None,
            "iadl_change": assessment.iadl_total_score - previous.iadl_total_score if previous and assessment.iadl_total_score is not None and previous.iadl_total_score is not None else None,
            "outing_change": assessment.outing_frequency - previous.outing_frequency if previous and assessment.outing_frequency is not None and previous.outing_frequency is not None else None,
            "burden_level": burden_item.text_value if burden_item else None,
            "burden_note": burden_item.note if burden_item else None,
            "env_risk_level": env_risk_item.text_value if env_risk_item else None,
            "env_risk_note": env_risk_item.note if env_risk_item else None,
        })
        previous = assessment
    care_groups = []
    for assessment in reversed(ordered_assessments):
        goals = [goal for goal in case.goals if goal.origin_assessment_id == assessment.id]
        continued_goal_ids = {goal.id for goal in goals if goal.predecessor_goal_id}
        continued_goal_labels = {
            goal.id: _continuation_label(goal) for goal in goals if goal.predecessor_goal_id
        }
        extended_plan_ids = {
            link.care_plan_id for link in db.query(CarePlanAssessmentLink).filter(
                CarePlanAssessmentLink.assessment_id == assessment.id
            ).all()
        }
        plans = [plan for plan in case.care_plans if plan.origin_assessment_id == assessment.id or plan.id in extended_plan_ids]
        care_groups.append({
            "label": f"{assessment.assessment_date}｜{assessment.assessment_type.value}",
            "assessment": assessment,
            "goals": goals,
            "plans": plans,
            "goal_plan_pairs": _goal_plan_pairs(goals, plans),
            "continued_goal_ids": continued_goal_ids,
            "continued_goal_labels": continued_goal_labels,
            "can_create_plan": any(goal.status == GoalStatus.in_progress for goal in goals),
        })
    legacy_goals = [goal for goal in case.goals if not goal.origin_assessment_id]
    legacy_plans = [plan for plan in case.care_plans if not plan.origin_assessment_id]
    if legacy_goals or legacy_plans:
        care_groups.append({
            "label": "既有未標示評估來源",
            "assessment": None,
            "goals": legacy_goals,
            "plans": legacy_plans,
            "goal_plan_pairs": _goal_plan_pairs(legacy_goals, legacy_plans),
            "continued_goal_ids": set(),
            "continued_goal_labels": {},
            "can_create_plan": any(goal.status == GoalStatus.in_progress for goal in legacy_goals),
        })
    contact_records = (
        db.query(ContactRecord)
        .filter(ContactRecord.case_id == case_id)
        .order_by(ContactRecord.contact_date.desc(), ContactRecord.created_at.desc())
        .all()
    )
    complaints = (
        db.query(Complaint)
        .filter(Complaint.case_id == case_id)
        .order_by(Complaint.received_date.desc(), Complaint.created_at.desc())
        .all()
    )
    imported_schedule_records = (
        db.query(CaregiverServiceRecord)
        .filter(CaregiverServiceRecord.case_id == case_id)
        .order_by(CaregiverServiceRecord.service_date.desc(), CaregiverServiceRecord.start_time.desc())
        .all()
    )
    pending_imported_schedule_count = sum(
        1 for record in imported_schedule_records
        if record.formalization_status == PENDING_FORMALIZATION
    )
    protected_goal_ids = {goal.predecessor_goal_id for goal in case.goals if goal.predecessor_goal_id}
    protected_plan_ids = {
        plan.predecessor_care_plan_id
        for plan in case.care_plans
        if plan.predecessor_care_plan_id
    }
    return templates.TemplateResponse(
        request,
        "case_detail.html",
        {
            "case": case,
            "user": user,
            "tab": tab,
            "assessment_trends": assessment_trends,
            "care_groups": care_groups,
            "protected_goal_ids": protected_goal_ids,
            "protected_plan_ids": protected_plan_ids,
            "return_logs": return_logs,
            "assessment_due": _assessment_due_context(case),
            "contact_records": contact_records,
            "complaints": complaints,
            "imported_schedule_records": imported_schedule_records,
            "pending_imported_schedule_count": pending_imported_schedule_count,
            "schedule_status_labels": STATUS_LABELS,
            "case_statuses": list(CaseStatus),
            "pause_reasons": list(PauseReasonType),
            "close_reasons": list(CloseReasonType),
            "today": date.today(),
            "schedule_warning": schedule_warning,
        },
    )


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "個案不存在")
    return case


@router.post("/{case_id}/status")
def update_case_status(
    case_id: str,
    status: str = Form(...),
    effective_date: str = Form(""),
    pause_reason_type: str = Form("other"),
    pause_reason_note: str = Form(""),
    close_reason_type: str = Form("other"),
    close_reason_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = _get_case_or_404(db, case_id)
    try:
        next_status = CaseStatus[status]
    except KeyError:
        raise HTTPException(400, "個案狀態不正確")
    effective = _parse_date(effective_date) or date.today()
    case.status = next_status
    if next_status == CaseStatus.active:
        case.resume_date = effective
        case.close_date = None
        case.close_reason_type = None
        case.close_reason_note = None
    elif next_status == CaseStatus.paused:
        case.pause_date = effective
        case.pause_reason_type = PauseReasonType[pause_reason_type] if pause_reason_type in PauseReasonType.__members__ else PauseReasonType.other
        case.pause_reason_note = pause_reason_note or None
        case.resume_date = None
        case.close_date = None
        case.close_reason_type = None
        case.close_reason_note = None
    elif next_status == CaseStatus.closed:
        case.close_date = effective
        case.close_reason_type = CloseReasonType[close_reason_type] if close_reason_type in CloseReasonType.__members__ else CloseReasonType.other
        case.close_reason_note = close_reason_note or None
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}?tab=basic", status_code=302)


@router.get("/{case_id}/pause", response_class=HTMLResponse)
def pause_case_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = _get_case_or_404(db, case_id)
    return templates.TemplateResponse(
        request, "case_pause_form.html", {"case": case, "user": user, "pause_reasons": list(PauseReasonType)}
    )


@router.post("/{case_id}/pause")
def pause_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    pause_date: str = Form(...),
    pause_reason_type: str = Form(...),
    pause_reason_note: str = Form(""),
):
    case = _get_case_or_404(db, case_id)
    case.status = CaseStatus.paused
    case.pause_date = _parse_date(pause_date)
    case.pause_reason_type = PauseReasonType[pause_reason_type]
    case.pause_reason_note = pause_reason_note or None
    case.resume_date = None
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.post("/{case_id}/resume")
def resume_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = _get_case_or_404(db, case_id)
    case.status = CaseStatus.active
    case.resume_date = date.today()
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.get("/{case_id}/close", response_class=HTMLResponse)
def close_case_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = _get_case_or_404(db, case_id)
    return templates.TemplateResponse(
        request, "case_close_form.html", {"case": case, "user": user, "close_reasons": list(CloseReasonType)}
    )


@router.post("/{case_id}/close")
def close_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    close_date: str = Form(...),
    close_reason_type: str = Form(...),
    close_reason_note: str = Form(""),
):
    case = _get_case_or_404(db, case_id)
    case.status = CaseStatus.closed
    case.close_date = _parse_date(close_date)
    case.close_reason_type = CloseReasonType[close_reason_type]
    case.close_reason_note = close_reason_note or None
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)


@router.get("/{case_id}/edit", response_class=HTMLResponse)
def edit_case_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
):
    case = _get_case_or_404(db, case_id)
    supervisors = db.query(User).filter(User.role.in_([UserRole.supervisor, UserRole.manager])).all()
    return templates.TemplateResponse(
        request, "case_edit_form.html", {"case": case, "user": user, "supervisors": supervisors, "error": None}
    )


@router.post("/{case_id}/edit")
def edit_case(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager, UserRole.director)),
    org_case_no: str = Form(...),
    name: str = Form(...),
    id_number: str = Form(...),
    birth_date: str = Form(""),
    gender: str = Form(""),
    phone: str = Form(""),
    ltc_welfare_status: str = Form(""),
    disability_category: str = Form(""),
    cms_level: str = Form(""),
    living_status: str = Form(""),
    residence_district: str = Form(""),
    household_address: str = Form(""),
    residence_address: str = Form(""),
    a_unit_name: str = Form(""),
    case_manager_name: str = Form(""),
    case_manager_contact: str = Form(""),
    last_cms_assessment_date: str = Form(""),
    primary_supervisor_id: str = Form(""),
    open_date: str = Form(""),
    line_group_id: str = Form(""),
    is_dialysis: str = Form("N"),
    dialysis_hospital_address: str = Form(""),
    dialysis_direction: str = Form(""),
):
    case = _get_case_or_404(db, case_id)
    supervisors = db.query(User).filter(User.role.in_([UserRole.supervisor, UserRole.manager])).all()

    duplicate = db.query(Case).filter(Case.org_case_no == org_case_no, Case.id != case_id).first()
    if duplicate:
        return templates.TemplateResponse(
            request, "case_edit_form.html",
            {"case": case, "user": user, "supervisors": supervisors, "error": f"機構案號「{org_case_no}」已被其他個案使用"},
            status_code=400,
        )

    case.org_case_no = org_case_no
    case.name = name
    case.id_number = id_number
    case.birth_date = _parse_date(birth_date)
    case.gender = gender or None
    case.phone = phone or None
    case.ltc_welfare_status = ltc_welfare_status or None
    case.disability_category = disability_category or None
    case.cms_level = cms_level or None
    case.living_status = living_status or None
    case.residence_district = residence_district or None
    case.household_address = household_address or None
    case.residence_address = residence_address or None
    case.a_unit_name = a_unit_name or None
    case.case_manager_name = case_manager_name or None
    case.case_manager_contact = case_manager_contact or None
    case.last_cms_assessment_date = _parse_date(last_cms_assessment_date)
    case.primary_supervisor_id = primary_supervisor_id or None
    case.open_date = _parse_date(open_date)
    case.line_group_id = line_group_id or None
    case.is_dialysis = is_dialysis
    case.dialysis_hospital_address = dialysis_hospital_address or None
    case.dialysis_direction = dialysis_direction or None
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)
