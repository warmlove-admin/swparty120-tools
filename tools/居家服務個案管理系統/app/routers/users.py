from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import hash_password, require_roles
from app.database import get_db
from app.models.user import User, UserRole

router = APIRouter(prefix="/users")
templates = Jinja2Templates(directory="app/templates")


def _page_context(user: User, db: Session, error: str | None = None):
    return {
        "user": user,
        "users": db.query(User).order_by(User.is_active.desc(), User.created_at.desc()).all(),
        "roles": list(UserRole),
        "error": error,
    }


@router.get("", response_class=HTMLResponse)
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager)),
):
    return templates.TemplateResponse(request, "users_list.html", _page_context(user, db))


@router.post("")
async def create_user(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager)),
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
        db.add(User(username=username, display_name=display_name, role=role, password_hash=hash_password(password)))
        db.commit()
        return RedirectResponse(url="/users", status_code=302)
    return templates.TemplateResponse(request, "users_list.html", _page_context(user, db, error), status_code=400)


@router.post("/{user_id}/update")
async def update_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager)),
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
    db.commit()
    return RedirectResponse(url="/users", status_code=302)


@router.post("/{user_id}/toggle-active")
def toggle_active(
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.manager)),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "找不到帳號")
    if target.id == user.id:
        raise HTTPException(400, "不可停用自己的帳號")
    target.is_active = not target.is_active
    db.commit()
    return RedirectResponse(url="/users", status_code=302)
