from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import cast, desc, func, Integer
from sqlalchemy.orm import Session
from datetime import date

from app.auth import hash_password, must_change_password, require_roles
from app.database import get_db
from app.models.user import User, UserRole
from app.services.attendance_engine import WEEKDAY_LABELS

router = APIRouter(prefix="/users")
templates = Jinja2Templates(directory="app/templates")


def _page_context(
    user: User,
    db: Session,
    error: str | None = None,
    selected_user_id: str | None = None,
    role_filter: str = "",
    status_filter: str = "active",
    keyword: str = "",
):
    users_query = db.query(User)
    selected_role = None
    if role_filter:
        try:
            selected_role = UserRole(role_filter)
            users_query = users_query.filter(User.role == selected_role)
        except ValueError:
            role_filter = ""
    if status_filter == "active":
        users_query = users_query.filter(User.is_active.is_(True))
    elif status_filter == "inactive":
        users_query = users_query.filter(User.is_active.is_(False))
    elif status_filter != "all":
        status_filter = "active"
        users_query = users_query.filter(User.is_active.is_(True))
    keyword = keyword.strip()
    if keyword:
        like = f"%{keyword}%"
        users_query = users_query.filter(
            (User.display_name.like(like)) | (User.username.like(like)) | (User.employee_no.like(like))
        )
    employee_no_number = cast(func.substr(User.employee_no, 2), Integer)
    users = users_query.order_by(
        User.employee_no.is_(None),
        desc(employee_no_number),
        User.display_name,
    ).all()
    selected_user = db.query(User).filter(User.id == selected_user_id).first() if selected_user_id else None
    if not selected_user and users:
        selected_user = users[0]
    return {
        "user": user,
        "users": users,
        "selected_user": selected_user,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "keyword": keyword,
        "roles": list(UserRole),
        "must_change_password": must_change_password,
        "weekday_labels": WEEKDAY_LABELS,
        "supervisors": db.query(User)
        .filter(User.role.in_([UserRole.supervisor, UserRole.manager, UserRole.director]), User.is_active.is_(True))
        .order_by(User.display_name)
        .all(),
        "error": error,
    }


def _parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def _apply_profile_form(target: User, form) -> None:
    target.employee_no = (form.get("employee_no") or "").strip() or None
    target.id_number = (form.get("id_number") or "").strip() or None
    target.gender = (form.get("gender") or "").strip() or None
    target.birth_date = _parse_date(form.get("birth_date"))
    target.phone = (form.get("phone") or "").strip() or None
    target.mobile = (form.get("mobile") or "").strip() or None
    target.email = (form.get("email") or "").strip() or None
    target.address = (form.get("address") or "").strip() or None
    target.job_title = (form.get("job_title") or "").strip() or None
    target.employment_status = (form.get("employment_status") or "").strip() or None
    target.hire_date = _parse_date(form.get("hire_date"))
    target.termination_date = _parse_date(form.get("termination_date"))
    target.supervisor_id = (form.get("supervisor_id") or "").strip() or None
    target.languages = ", ".join(form.getlist("languages")) or None
    target.emergency_contact_name = (form.get("emergency_contact_name") or "").strip() or None
    target.emergency_contact_relation = (form.get("emergency_contact_relation") or "").strip() or None
    target.emergency_contact_phone = (form.get("emergency_contact_phone") or "").strip() or None
    target.note = (form.get("note") or "").strip() or None
    target.regular_off_weekday = int(form.get("regular_off_weekday")) if form.get("regular_off_weekday") else None
    target.rest_weekday = int(form.get("rest_weekday")) if form.get("rest_weekday") else None
    target.hourly_wage = int(form.get("hourly_wage")) if form.get("hourly_wage") else None
    wkdays = form.getlist("work_weekdays")
    target.work_weekdays = sorted({int(v) for v in wkdays if v.strip()}) if wkdays else None


@router.get("", response_class=HTMLResponse)
def list_users(
    request: Request,
    selected_user_id: str = "",
    role_filter: str = "",
    status_filter: str = "active",
    keyword: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    return templates.TemplateResponse(
        request,
        "users_list.html",
        _page_context(user, db, selected_user_id=selected_user_id, role_filter=role_filter, status_filter=status_filter, keyword=keyword),
    )


@router.post("")
async def create_user(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    form = await request.form()
    username = (form.get("username") or "").strip()
    display_name = (form.get("display_name") or "").strip()
    password = form.get("password") or ""
    role_value = form.get("role") or ""
    try:
        role = UserRole(role_value)
    except ValueError:
        role = None

    if not username or not display_name or not role:
        error = "請填寫帳號、顯示姓名與角色。"
    elif len(password) < 8:
        error = "初始密碼至少需 8 個字元。"
    elif db.query(User).filter(User.username == username).first():
        error = "此帳號已存在，請改用其他帳號。"
    else:
        new_user = User(
            username=username,
            display_name=display_name,
            role=role,
            password_hash=hash_password(password),
            must_change_password=True,
        )
        _apply_profile_form(new_user, form)
        db.add(new_user)
        db.commit()
        return RedirectResponse(url=f"/users?selected_user_id={new_user.id}&status_filter=all", status_code=302)
    return templates.TemplateResponse(request, "users_list.html", _page_context(user, db, error), status_code=400)


@router.post("/{user_id}/update")
async def update_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "找不到帳號")
    form = await request.form()
    display_name = (form.get("display_name") or "").strip()
    try:
        role = UserRole(form.get("role") or "")
    except ValueError:
        return templates.TemplateResponse(request, "users_list.html", _page_context(user, db, "角色資料不正確。"), status_code=400)
    if not display_name:
        return templates.TemplateResponse(request, "users_list.html", _page_context(user, db, "顯示姓名不可空白。"), status_code=400)
    if target.id == user.id and role != UserRole.manager:
        return templates.TemplateResponse(request, "users_list.html", _page_context(user, db, "不可將自己的主管權限降級。"), status_code=400)
    target.display_name = display_name
    target.role = role
    _apply_profile_form(target, form)
    db.commit()
    return RedirectResponse(url=f"/users?selected_user_id={target.id}&status_filter=all", status_code=302)


@router.post("/{user_id}/toggle-active")
def toggle_active(
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "找不到帳號")
    if target.id == user.id:
        raise HTTPException(400, "不可停用自己的帳號")
    target.is_active = not target.is_active
    db.commit()
    return RedirectResponse(url=f"/users?selected_user_id={target.id}&status_filter=all", status_code=302)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager, UserRole.director)),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "找不到帳號")
    form = await request.form()
    new_password = form.get("new_password") or ""
    confirm_password = form.get("confirm_password") or ""
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request,
            "users_list.html",
            _page_context(user, db, "臨時密碼至少需 8 個字元。", selected_user_id=target.id, status_filter="all"),
            status_code=400,
        )
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request,
            "users_list.html",
            _page_context(user, db, "兩次臨時密碼不一致。", selected_user_id=target.id, status_filter="all"),
            status_code=400,
        )
    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    db.commit()
    return RedirectResponse(url=f"/users?selected_user_id={target.id}&status_filter=all&password_reset=1", status_code=302)
