from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.case import Case, CaseStatus, PauseReasonType, CloseReasonType
from app.models.care_plan import CarePlan
from app.models.contact import Contact, ContactRole
from app.models.user import User, UserRole
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


@router.get("", response_class=HTMLResponse)
def list_cases(
    request: Request,
    status_filter: str = "active",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    valid_filters = {"active": CaseStatus.active, "paused": CaseStatus.paused, "closed": CaseStatus.closed, "all": None}
    if status_filter not in valid_filters:
        status_filter = "active"
    query = visible_cases_query(db, user)
    if valid_filters[status_filter] is not None:
        query = query.filter(Case.status == valid_filters[status_filter])
    cases = query.order_by(Case.created_at.desc()).all()
    return templates.TemplateResponse(request, "cases_list.html", {"cases": cases, "user": user, "status_filter": status_filter})


@router.get("/new", response_class=HTMLResponse)
def new_case_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    supervisors = db.query(User).filter(User.role.in_([UserRole.supervisor, UserRole.manager])).all()
    return templates.TemplateResponse(
        request, "case_form.html", {"user": user, "supervisors": supervisors, "error": None, "prefill": None}
    )


@router.post("/import", response_class=HTMLResponse)
async def import_ltc_html(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
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
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
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
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = visible_cases_query(db, user).filter(Case.id == case_id).first()
    if not case:
        return RedirectResponse(url="/cases", status_code=302)
    if tab not in {"basic", "assessment", "care", "schedule"}:
        tab = "basic"
    return templates.TemplateResponse(request, "case_detail.html", {"case": case, "user": user, "tab": tab})


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "個案不存在")
    return case


@router.get("/{case_id}/pause", response_class=HTMLResponse)
def pause_case_form(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    return templates.TemplateResponse(
        request, "case_pause_form.html", {"case": case, "user": user, "pause_reasons": list(PauseReasonType)}
    )


@router.post("/{case_id}/pause")
def pause_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
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
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
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
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
):
    case = _get_case_or_404(db, case_id)
    return templates.TemplateResponse(
        request, "case_close_form.html", {"case": case, "user": user, "close_reasons": list(CloseReasonType)}
    )


@router.post("/{case_id}/close")
def close_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
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
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
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
    user: User = Depends(require_roles(UserRole.supervisor, UserRole.manager)),
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
    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=302)
