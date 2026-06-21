from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.assessment import Assessment, AssessmentItem, AssessmentType
from app.models.case import Case
from app.models.user import User, UserRole
from app.services.assessment_catalog import (
    ADL_ITEMS, ADL_MAX_SCORE, IADL_ITEMS, IADL_MAX_SCORE, COGNITIVE_ITEMS,
    FAMILY_BURDEN_CHECKLIST, FAMILY_OTHER_ITEMS, ENV_RISK_CHECKLIST,
    ENV_RISK_OPTIONS, ENV_OTHER_ITEMS, ECON_ITEMS, CULTURE_ITEMS,
    family_burden_level, env_risk_level, dependency_level,
)

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


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "個案不存在")
    return case


@router.get("/new", response_class=HTMLResponse)
def new_assessment_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    return templates.TemplateResponse(
        request,
        "assessment_form.html",
        {
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
        },
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

    assessment_date = form.get("assessment_date")
    outing_frequency = form.get("outing_frequency")

    assessment = Assessment(
        case_id=case.id,
        assessment_type=AssessmentType.intake,
        assessment_date=date.fromisoformat(assessment_date),
        assessor_id=user.id,
        outing_frequency=float(outing_frequency) if outing_frequency else None,
    )
    db.add(assessment)
    db.flush()

    # ADL：巴氏量表，10項加總滿分100，可直接當總分
    adl_raw = 0.0
    for code, label, options in ADL_ITEMS:
        value = form.get(f"score_{code}")
        score = float(value) if value else 0.0
        adl_raw += score
        desc = next((d for s, d in options if s == int(score)), "")
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["ADL"],
            item_code=code, score_value=score, note=desc,
        ))

    # IADL：Lawton量表，各項權重不一，加總後換算為百分制方便與ADL對照呈現
    iadl_raw = 0.0
    for code, label, options in IADL_ITEMS:
        value = form.get(f"score_{code}")
        score = float(value) if value else 0.0
        iadl_raw += score
        desc = next((d for s, d in options if s == int(score)), "")
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["IADL"],
            item_code=code, score_value=score, note=desc,
        ))

    for code, _label, _options in COGNITIVE_ITEMS:
        text_value = form.get(f"select_{code}")
        note = form.get(f"note_{code}")
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["COG"],
            item_code=code, text_value=text_value, note=note or None,
        ))

    # 3.3 家庭照顧者負荷：客觀checklist計分 + 主觀感受分數，換算等級後存為一個項目，
    # 各checklist子項也分別記錄供日後查核依據
    burden_yes_count = 0
    for code, label in FAMILY_BURDEN_CHECKLIST:
        checked = form.get(f"burden_{code}") == "1"
        if checked:
            burden_yes_count += 1
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["FAM"],
            item_code=code, text_value="是" if checked else "否",
        ))
    subjective_score = int(form.get("burden_subjective_score") or 0)
    burden_level = family_burden_level(burden_yes_count, subjective_score)
    db.add(AssessmentItem(
        assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["FAM"],
        item_code="FAM_burden_level", text_value=burden_level,
        note=f"客觀項目{burden_yes_count}項＋主觀感受{subjective_score}分，總分{burden_yes_count + subjective_score}",
    ))

    for code, _label, _options in FAMILY_OTHER_ITEMS:
        text_value = form.get(f"select_{code}")
        note = form.get(f"note_{code}")
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["FAM"],
            item_code=code, text_value=text_value, note=note or None,
        ))

    # 3.4 居家環境風險：checklist計算「有風險」項目數（排除不適用），換算風險等級
    risk_count = 0
    applicable_count = 0
    for code, label in ENV_RISK_CHECKLIST:
        value = form.get(f"envrisk_{code}") or ENV_RISK_OPTIONS[1]
        if value == ENV_RISK_OPTIONS[0]:
            risk_count += 1
            applicable_count += 1
        elif value == ENV_RISK_OPTIONS[1]:
            applicable_count += 1
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["ENV"],
            item_code=code, text_value=value,
        ))
    risk_level = env_risk_level(risk_count)
    db.add(AssessmentItem(
        assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["ENV"],
        item_code="ENV_risk_level", text_value=risk_level,
        note=f"{applicable_count}項適用項目中，{risk_count}項有風險",
    ))

    for code, _label, _options in ENV_OTHER_ITEMS:
        text_value = form.get(f"select_{code}")
        note = form.get(f"note_{code}")
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["ENV"],
            item_code=code, text_value=text_value, note=note or None,
        ))

    for catalog, prefix in [(ECON_ITEMS, "ECON"), (CULTURE_ITEMS, "CUL")]:
        for code, _label, _options in catalog:
            text_value = form.get(f"select_{code}") or None
            note = form.get(f"note_{code}")
            db.add(AssessmentItem(
                assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX[prefix],
                item_code=code, text_value=text_value, note=note or None,
            ))

    culture_note = form.get("note_CUL_note")
    if culture_note:
        db.add(AssessmentItem(
            assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["CUL"],
            item_code="CUL_note", note=culture_note,
        ))

    assessment.adl_total_score = int(adl_raw)  # 滿分100，可直接當總分
    assessment.iadl_total_score = round(iadl_raw / IADL_MAX_SCORE * 100)  # 換算為百分制（顯示用）

    # 依賴等級：ADL用原始0-100分，IADL用原始0-31分（未換算百分制）查表，
    # 兩者各自查表後取較嚴重者
    db.add(AssessmentItem(
        assessment_id=assessment.id, domain=DOMAIN_BY_PREFIX["ADL"],
        item_code="dependency_level", text_value=dependency_level(int(adl_raw), int(iadl_raw)),
        note="依ADL（0-100）與IADL（0-31）對照依賴等級表，取兩者中較嚴重的等級",
    ))

    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}/assessments/{assessment.id}", status_code=302)


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
    return templates.TemplateResponse(request, "assessment_detail.html", {"case": case, "assessment": assessment, "user": user})
