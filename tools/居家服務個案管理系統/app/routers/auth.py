from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import COOKIE_NAME, create_access_token, get_current_user, hash_password, must_change_password, verify_password
from app.database import get_db
from app.models.user import User

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "帳號或密碼錯誤"},
            status_code=401,
        )

    token = create_access_token(user.id)
    redirect = RedirectResponse(url="/", status_code=302)
    redirect.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # 部署到正式雲端網域（https）時應改為 True
        max_age=12 * 3600,
    )
    if must_change_password(user):
        redirect = RedirectResponse(url="/change-password", status_code=302)
        redirect.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=12 * 3600,
        )
        return redirect
    return redirect


@router.get("/change-password", response_class=HTMLResponse)
def change_password_form(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        request,
        "change_password.html",
        {"user": user, "error": None, "password_change_required": must_change_password(user)},
    )


@router.post("/change-password")
def change_password_submit(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not must_change_password(user) and not verify_password(current_password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"user": user, "error": "目前密碼不正確", "password_change_required": must_change_password(user)},
            status_code=400,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"user": user, "error": "新密碼至少需 8 個字元", "password_change_required": must_change_password(user)},
            status_code=400,
        )
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"user": user, "error": "兩次新密碼不一致", "password_change_required": must_change_password(user)},
            status_code=400,
        )
    user.password_hash = hash_password(new_password)
    user.must_change_password = None
    db.commit()
    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
def logout():
    redirect = RedirectResponse(url="/login", status_code=302)
    redirect.delete_cookie(COOKIE_NAME)
    return redirect
