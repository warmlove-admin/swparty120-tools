from copy import deepcopy
from datetime import date
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.assessment import Assessment, AssessmentItem, AssessmentType, RecordStatus
from app.models.case import Case
from app.models.care_plan import CarePlanAssessmentLink
from app.models.care_plan import CarePlan
from app.models.goal import AchievementLevel, Goal, GoalDecision, GoalProgressLog, GoalStatus
from app.models.record_status_log import RecordStatusLog
from app.models.service_schedule import ServiceSchedule
from app.models.user import User, UserRole
from app.services.assessment_catalog import (
    ADL_ITEMS, ADL_MAX_SCORE, IADL_ITEMS, IADL_MAX_SCORE, COGNITIVE_ITEMS,
    FAMILY_BURDEN_CHECKLIST, FAMILY_OTHER_ITEMS, ENV_RISK_CHECKLIST,
    ENV_RISK_OPTIONS, ENV_OTHER_ITEMS, ECON_ITEMS, CULTURE_ITEMS,
    family_burden_level, env_risk_level, dependency_level,
)
from app.services.assessment_summary import build_assessment_summary
from app.services.signature_stamps import review_rows

router = APIRouter(prefix="/cases/{case_id}/assessments")
templates = Jinja2Templates(directory="app/templates")

DOMAIN_BY_PREFIX = {
    "ADL": "身體功能面",
    "IADL": "身體功能面",
    "COG": "認知與心理面",
    "FAM": "家庭與照顧者面",
    "ENV": "居家環境面",
    "ECON": "福利資源與服務滿意度",
    "CUL": "文化與語言面",
}


def _assessment_form_context(
    case: Case,
    user: User,
    assessment_type: AssessmentType,
    assessment: Assessment | None = None,
    prefill_source: Assessment | None = None,
) -> dict:
    form_values = {}
    if assessment:
        form_values = _assessment_form_values(assessment)
    elif prefill_source:
        form_values = _assessment_form_values(prefill_source)
        form_values.pop("assessment_date", None)
    return {
        "case": case,
        "user": user,
        "adl_items": ADL_ITEMS,
        "iadl_items": IADL_ITEMS,
        "cognitive_items": COGNITIVE_ITEMS,
        "family_burden_checklist": FAMILY_BURDEN_CHECKLIST,
        "family_other_items": FAMILY_OTHER_ITEMS,
        "env_risk_checklist": ENV_RISK_CHECKLIST,
        "env_risk_options": ENV_RISK_OPTIONS,
        "env_other_items": ENV_OTHER_ITEMS,
        "econ_items": ECON_ITEMS,
        "culture_items": CULTURE_ITEMS,
        "assessment_type": assessment_type,
        "assessment": assessment,
        "prefill_source": prefill_source,
        "form_values": form_values,
    }


def _score_text(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _assessment_form_values(assessment: Assessment) -> dict:
    values = {
        "assessment_date": assessment.assessment_date.isoformat(),
        "outing_frequency": _score_text(assessment.outing_frequency),
        "burden_subjective_score": "0",
    }
    burden_note = next((item.note for item in assessment.items if item.item_code == "FAM_burden_level" and item.note), "")
    match = re.search(r"主觀感受(\d+)分", burden_note)
    if match:
        values["burden_subjective_score"] = match.group(1)
    burden_codes = {code for code, _label in FAMILY_BURDEN_CHECKLIST}
    env_risk_codes = {code for code, _label in ENV_RISK_CHECKLIST}
    for item in assessment.items:
        if item.item_code in {"dependency_level", "FAM_burden_level", "ENV_risk_level", "summary_override"}:
            continue
        if item.score_value is not None:
            values[f"score_{item.item_code}"] = _score_text(item.score_value)
        if item.item_code in burden_codes:
            values[f"burden_{item.item_code}"] = "1" if item.text_value == "是" else "0"
        elif item.item_code in env_risk_codes:
            values[f"envrisk_{item.item_code}"] = item.text_value or ""
        elif item.text_value is not None:
            values[f"select_{item.item_code}"] = item.text_value
        if item.item_code == "CUL_note":
            values["note_CUL_note"] = item.note or ""
        elif item.note:
            values[f"note_{item.item_code}"] = item.note
    return values


def _apply_assessment_form(assessment: Assessment, form) -> None:
    assessment.assessment_date = date.fromisoformat(form.get("assessment_date"))
    outing_frequency = form.get("outing_frequency")
    assessment.outing_frequency = float(outing_frequency) if outing_frequency else None

    kept_summary_items = [item for item in assessment.items if item.item_code == "summary_override"]
    assessment.items = kept_summary_items

    # ADL：巴氏量表，10項加總滿分100，可直接當總分
    adl_raw = 0.0
    for code, _label, options in ADL_ITEMS:
        value = form.get(f"score_{code}")
        score = float(value) if value else 0.0
        adl_raw += score
        desc = next((d for s, d in options if s == int(score)), "")
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["ADL"],
            item_code=code,
            score_value=score,
            note=desc,
        ))

    # IADL：Lawton量表，各項權重不一，加總後換算為百分制方便與ADL對照呈現
    iadl_raw = 0.0
    for code, _label, options in IADL_ITEMS:
        value = form.get(f"score_{code}")
        score = float(value) if value else 0.0
        iadl_raw += score
        desc = next((d for s, d in options if s == int(score)), "")
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["IADL"],
            item_code=code,
            score_value=score,
            note=desc,
        ))

    for code, _label, _options in COGNITIVE_ITEMS:
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["COG"],
            item_code=code,
            text_value=form.get(f"select_{code}"),
            note=form.get(f"note_{code}") or None,
        ))

    burden_yes_count = 0
    for code, _label in FAMILY_BURDEN_CHECKLIST:
        checked = form.get(f"burden_{code}") == "1"
        if checked:
            burden_yes_count += 1
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["FAM"],
            item_code=code,
            text_value="是" if checked else "否",
        ))
    subjective_score = int(form.get("burden_subjective_score") or 0)
    assessment.items.append(AssessmentItem(
        domain=DOMAIN_BY_PREFIX["FAM"],
        item_code="FAM_burden_level",
        text_value=family_burden_level(burden_yes_count, subjective_score),
        note=f"客觀項目{burden_yes_count}項＋主觀感受{subjective_score}分，總分{burden_yes_count + subjective_score}",
    ))

    for code, _label, _options in FAMILY_OTHER_ITEMS:
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["FAM"],
            item_code=code,
            text_value=form.get(f"select_{code}"),
            note=form.get(f"note_{code}") or None,
        ))

    risk_count = 0
    applicable_count = 0
    for code, _label in ENV_RISK_CHECKLIST:
        value = form.get(f"envrisk_{code}") or ENV_RISK_OPTIONS[1]
        if value == ENV_RISK_OPTIONS[0]:
            risk_count += 1
            applicable_count += 1
        elif value == ENV_RISK_OPTIONS[1]:
            applicable_count += 1
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["ENV"],
            item_code=code,
            text_value=value,
        ))
    assessment.items.append(AssessmentItem(
        domain=DOMAIN_BY_PREFIX["ENV"],
        item_code="ENV_risk_level",
        text_value=env_risk_level(risk_count),
        note=f"{applicable_count}項適用項目中，{risk_count}項有風險",
    ))

    for code, _label, _options in ENV_OTHER_ITEMS:
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["ENV"],
            item_code=code,
            text_value=form.get(f"select_{code}"),
            note=form.get(f"note_{code}") or None,
        ))

    for catalog, prefix in [(ECON_ITEMS, "ECON"), (CULTURE_ITEMS, "CUL")]:
        for code, _label, _options in catalog:
            assessment.items.append(AssessmentItem(
                domain=DOMAIN_BY_PREFIX[prefix],
                item_code=code,
                text_value=form.get(f"select_{code}") or None,
                note=form.get(f"note_{code}") or None,
            ))

    culture_note = form.get("note_CUL_note")
    if culture_note:
        assessment.items.append(AssessmentItem(
            domain=DOMAIN_BY_PREFIX["CUL"],
            item_code="CUL_note",
            note=culture_note,
        ))

    assessment.adl_total_score = int(adl_raw)
    assessment.iadl_total_score = round(iadl_raw / IADL_MAX_SCORE * 100)
    assessment.items.append(AssessmentItem(
        domain=DOMAIN_BY_PREFIX["ADL"],
        item_code="dependency_level",
        text_value=dependency_level(int(adl_raw), int(iadl_raw)),
        note="依ADL（0-100）與IADL（0-31）對照依賴等級表，取兩者中較嚴重的等級",
    ))


def _assessment_trends(db: Session, case_id: str) -> list[dict]:
    ordered_assessments = db.query(Assessment).filter(
        Assessment.case_id == case_id
    ).order_by(Assessment.assessment_date).all()
    trends = []
    previous = None
    for assessment in ordered_assessments:
        trends.append({
            "assessment": assessment,
            "adl_change": assessment.adl_total_score - previous.adl_total_score if previous and assessment.adl_total_score is not None and previous.adl_total_score is not None else None,
            "iadl_change": assessment.iadl_total_score - previous.iadl_total_score if previous and assessment.iadl_total_score is not None and previous.iadl_total_score is not None else None,
        })
        previous = assessment
    return trends


def _review_note_label(log: RecordStatusLog) -> str:
    if log.to_status == RecordStatus.pending.value:
        return "居督送審／重送審"
    if log.to_status == RecordStatus.draft.value and log.from_status == RecordStatus.approved.value:
        return "定案後退回重修"
    if log.to_status == RecordStatus.draft.value:
        return "審核退回補正"
    if log.to_status == RecordStatus.approved.value:
        return "主管／主任核閱"
    return "流程紀錄"


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "個案不存在")
    return case


@router.get("/new", response_class=HTMLResponse)
def new_assessment_form(
    case_id: str,
    request: Request,
    assessment_type: str = "intake",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    try:
        selected_type = AssessmentType(assessment_type)
    except ValueError:
        selected_type = AssessmentType.intake
    prefill_source = None
    if selected_type == AssessmentType.periodic:
        prefill_source = db.query(Assessment).filter(
            Assessment.case_id == case_id
        ).order_by(Assessment.assessment_date.desc()).first()
    return templates.TemplateResponse(
        request,
        "assessment_form.html",
        _assessment_form_context(case, user, selected_type, prefill_source=prefill_source),
    )


@router.post("")
async def create_assessment(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    form = await request.form()

    try:
        assessment_type = AssessmentType(form.get("assessment_type") or AssessmentType.intake.value)
    except ValueError:
        assessment_type = AssessmentType.intake
    assessment = Assessment(
        case_id=case.id,
        assessment_type=assessment_type,
        assessment_date=date.today(),
        assessor_id=user.id,
    )
    db.add(assessment)
    db.flush()
    _apply_assessment_form(assessment, form)
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}/assessments/{assessment.id}", status_code=302)


@router.get("/{assessment_id}/edit", response_class=HTMLResponse)
def edit_assessment_form(
    case_id: str,
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    if assessment.status != RecordStatus.draft:
        raise HTTPException(400, "只有草稿評估可以編輯；已送審或已核閱資料請先退回草稿。")
    return templates.TemplateResponse(
        request,
        "assessment_form.html",
        _assessment_form_context(case, user, assessment.assessment_type, assessment),
    )


@router.post("/{assessment_id}/edit")
async def edit_assessment(
    case_id: str,
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    if assessment.status != RecordStatus.draft:
        raise HTTPException(400, "只有草稿評估可以編輯；已送審或已核閱資料請先退回草稿。")
    form = await request.form()
    _apply_assessment_form(assessment, form)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}/assessments/{assessment.id}", status_code=302)


@router.get("/{assessment_id}/goal-review", response_class=HTMLResponse)
def goal_review_form(
    case_id: str,
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    existing_logs = {
        log.goal_id: log for log in db.query(GoalProgressLog).filter(GoalProgressLog.assessment_id == assessment_id).all()
    }
    # 已在本次評估檢討過、且已被標為達成／更換的目標，仍須能回來修改原判定。
    goals = db.query(Goal).outerjoin(GoalProgressLog, GoalProgressLog.goal_id == Goal.id).filter(
        Goal.case_id == case_id,
        or_(Goal.origin_assessment_id.is_(None), Goal.origin_assessment_id != assessment.id),
        or_(Goal.status == GoalStatus.in_progress, GoalProgressLog.assessment_id == assessment_id),
    ).distinct().order_by(Goal.set_date).all()
    return templates.TemplateResponse(
        request, "goal_review_form.html",
        {"case": case, "assessment": assessment, "goals": goals, "existing_logs": existing_logs, "user": user},
    )


@router.post("/{assessment_id}/goal-review")
async def save_goal_review(
    case_id: str,
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    form = await request.form()
    existing_goal_ids = {
        log.goal_id for log in db.query(GoalProgressLog.goal_id).filter(GoalProgressLog.assessment_id == assessment_id).all()
    }
    goals = db.query(Goal).filter(
        Goal.case_id == case_id,
        or_(Goal.origin_assessment_id.is_(None), Goal.origin_assessment_id != assessment.id),
        or_(Goal.status == GoalStatus.in_progress, Goal.id.in_(existing_goal_ids)),
    ).all()
    errors = []
    prepared = []
    for goal in goals:
        try:
            achievement = AchievementLevel(form.get(f"achievement_{goal.id}"))
            decision = GoalDecision(form.get(f"decision_{goal.id}"))
        except ValueError:
            errors.append(f"「{goal.description}」的達成情形或後續決定不正確。")
            continue
        reason = (form.get(f"reason_{goal.id}") or "").strip()
        # 只有「達成後結案」可不填說明；其餘判斷必須留下專業依據。
        if (achievement != AchievementLevel.achieved or decision != GoalDecision.close) and not reason:
            errors.append(f"「{goal.description}」尚未完全達成或要延用／更換，請填寫原因或評估說明。")
        prepared.append((goal, achievement, decision, reason))
    if errors:
        existing_logs = {log.goal_id: log for log in db.query(GoalProgressLog).filter(GoalProgressLog.assessment_id == assessment_id).all()}
        return templates.TemplateResponse(
            request, "goal_review_form.html",
            {"case": case, "assessment": assessment, "goals": goals, "existing_logs": existing_logs, "user": user, "errors": errors, "submitted": form},
            status_code=422,
        )
    reference = f"本次評估：ADL {assessment.adl_total_score if assessment.adl_total_score is not None else '-'}／100；IADL {assessment.iadl_total_score if assessment.iadl_total_score is not None else '-'}／100。"
    continued_goal_copies = {}
    for goal, achievement, decision, reason in prepared:
        log = db.query(GoalProgressLog).filter(
            GoalProgressLog.goal_id == goal.id, GoalProgressLog.assessment_id == assessment.id
        ).first()
        if not log:
            log = GoalProgressLog(goal_id=goal.id, assessment_id=assessment.id)
            db.add(log)
        log.achievement_level = achievement
        log.decision = decision
        log.change_reason = reason or None
        log.system_reference_summary = reference
        if decision == GoalDecision.close:
            goal.status = GoalStatus.achieved if achievement == AchievementLevel.achieved else GoalStatus.replaced
            _remove_stale_continuations(db, goal, assessment)
        elif decision == GoalDecision.replace:
            goal.status = GoalStatus.replaced
            _remove_stale_continuations(db, goal, assessment)
        else:
            # 沿用不是讓舊目標跨期顯示：保留舊目標，系統自動產生下一期可再調整的新目標。
            goal.status = GoalStatus.continued
            copy_goal = db.query(Goal).filter(
                Goal.predecessor_goal_id == goal.id,
                Goal.origin_assessment_id == assessment.id,
            ).first()
            if not copy_goal:
                copy_goal = Goal(
                    case_id=case.id,
                    template_id=goal.template_id,
                    origin_assessment_id=assessment.id,
                    predecessor_goal_id=goal.id,
                    domain=goal.domain,
                    description=goal.description,
                    related_item_codes=deepcopy(goal.related_item_codes),
                    set_date=assessment.assessment_date,
                    status=GoalStatus.in_progress,
                )
                db.add(copy_goal)
                db.flush()
            continued_goal_copies[goal.id] = copy_goal

    # 同一份計畫可對應多個目標；每份舊計畫只複製一次，再連結所有本次自動承接的新目標。
    for old_goal_id, new_goal in continued_goal_copies.items():
        old_goal = next(goal for goal, *_ in prepared if goal.id == old_goal_id)
        for old_plan in old_goal.care_plans:
            copied_goal_ids = {goal.id for goal in old_plan.goals if goal.id in continued_goal_copies}
            if not copied_goal_ids:
                continue
            new_plan = db.query(CarePlan).filter(
                CarePlan.predecessor_care_plan_id == old_plan.id,
                CarePlan.origin_assessment_id == assessment.id,
            ).first()
            if not new_plan:
                new_plan = CarePlan(
                    case_id=case.id,
                    predecessor_care_plan_id=old_plan.id,
                    origin_assessment_id=assessment.id,
                    coded_services=deepcopy(old_plan.coded_services),
                    assigned_caregiver_id=old_plan.assigned_caregiver_id,
                    note=old_plan.note,
                )
                db.add(new_plan)
                db.flush()
            new_plan.goals = [continued_goal_copies[goal_id] for goal_id in copied_goal_ids]
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}?tab=care&reviewed_assessment={assessment_id}", status_code=302)


def _remove_stale_continuations(db: Session, goal: Goal, assessment: Assessment) -> None:
    stale_goals = db.query(Goal).filter(
        Goal.predecessor_goal_id == goal.id,
        Goal.origin_assessment_id == assessment.id,
    ).all()
    for stale_goal in stale_goals:
        has_later_review = db.query(GoalProgressLog).filter(
            GoalProgressLog.goal_id == stale_goal.id,
            GoalProgressLog.assessment_id != assessment.id,
        ).first()
        if has_later_review:
            stale_goal.status = GoalStatus.replaced
            continue

        for plan in list(stale_goal.care_plans):
            if plan.origin_assessment_id == assessment.id and plan.predecessor_care_plan_id:
                plan.goals.remove(stale_goal)
                if not plan.goals:
                    db.query(ServiceSchedule).filter(ServiceSchedule.care_plan_id == plan.id).delete()
                    db.delete(plan)
        stale_goal.care_plans.clear()
        db.query(GoalProgressLog).filter(GoalProgressLog.goal_id == stale_goal.id).delete()
        db.delete(stale_goal)


@router.get("/{assessment_id}", response_class=HTMLResponse)
def assessment_detail(
    case_id: str,
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = _get_case_or_404(db, case_id)
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    override = next((item.note for item in assessment.items if item.item_code == "summary_override" and item.note), None)
    active_goals = db.query(Goal).filter(Goal.case_id == case_id, Goal.status == GoalStatus.in_progress).count()
    can_create_care_plan = db.query(Goal).filter(
        Goal.case_id == case_id,
        Goal.origin_assessment_id == assessment_id,
        Goal.status == GoalStatus.in_progress,
    ).first() is not None
    review_count = db.query(GoalProgressLog).filter(GoalProgressLog.assessment_id == assessment_id).count()
    review_logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "assessment", RecordStatusLog.record_id == assessment_id
    ).order_by(RecordStatusLog.created_at.desc()).all()
    workflow_logs = list(reversed(review_logs))
    return templates.TemplateResponse(
        request,
        "assessment_detail.html",
        {
            "case": case,
            "assessment": assessment,
            "user": user,
            "summary_text": override or build_assessment_summary(assessment),
            "assessment_trends": _assessment_trends(db, case_id),
            "active_goals": active_goals,
            "can_create_care_plan": can_create_care_plan,
            "review_count": review_count,
            "review_logs": review_logs,
            "active_review_rows": review_rows(workflow_logs, active_only=True),
            "review_note_label": _review_note_label,
        },
    )


@router.post("/{assessment_id}/delete")
def delete_assessment(
    case_id: str,
    assessment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    has_reviews = db.query(GoalProgressLog).filter(GoalProgressLog.assessment_id == assessment_id).count()
    has_goals = db.query(Goal).filter(Goal.origin_assessment_id == assessment_id).count()
    has_plans = db.query(CarePlan).filter(CarePlan.origin_assessment_id == assessment_id).count()
    if has_reviews or has_goals or has_plans:
        raise HTTPException(400, "此評估已連結目標、照顧計畫或目標檢討，請先處理後續紀錄，不能直接刪除。")
    db.query(CarePlanAssessmentLink).filter(CarePlanAssessmentLink.assessment_id == assessment_id).delete()
    db.delete(assessment)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}?tab=assessment", status_code=302)


@router.post("/{assessment_id}/summary")
async def save_summary(
    case_id: str,
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.case_id == case_id).first()
    if not assessment:
        raise HTTPException(404, "評估紀錄不存在")
    form = await request.form()
    summary_text = (form.get("summary_text") or "").strip()
    existing = next((item for item in assessment.items if item.item_code == "summary_override"), None)
    if existing:
        existing.note = summary_text or None
    elif summary_text:
        db.add(AssessmentItem(assessment_id=assessment.id, domain="綜合評估摘要", item_code="summary_override", note=summary_text))
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}/assessments/{assessment_id}", status_code=302)
