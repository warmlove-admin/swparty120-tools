from fastapi import FastAPI, Depends, Request, status
from fastapi.exceptions import HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt

from app.database import Base, engine, SessionLocal, apply_compatible_schema_updates
from app import models  # noqa: F401  (ensures all models are registered)
from app.auth import ALGORITHM, COOKIE_NAME, get_current_user, must_change_password
from app.config import settings
from app.models.user import User
from app.routers import auth as auth_router
from app.routers import cases as cases_router
from app.routers import assessments as assessments_router
from app.routers import goals as goals_router
from app.routers import care_plans as care_plans_router
from app.routers import schedules as schedules_router
from app.routers import users as users_router
from app.routers import reviews as reviews_router
from app.routers import contact_records as contact_records_router
from app.routers import exports as exports_router
from app.routers import complaints as complaints_router
from app.routers import leave_calc as leave_calc_router
from app.services.goal_template_seed import seed_goal_templates_if_empty
from app.models.user import UserRole

app = FastAPI(title="居家服務個案管理系統")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def force_password_change(request: Request, call_next):
    allowed_paths = ("/login", "/logout", "/change-password", "/health", "/line/webhook", "/static")
    if request.url.path.startswith(allowed_paths):
        return await call_next(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
        except JWTError:
            user_id = None
        if user_id:
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
                if user and must_change_password(user):
                    return RedirectResponse(url="/change-password", status_code=302)
            finally:
                db.close()
    return await call_next(request)


def include_project_router(router):
    """FastAPI 0.138 keeps included routers deferred; expose concrete routes for this app."""
    app.router.routes.extend(router.routes)
    app.router._mark_routes_changed()


include_project_router(auth_router.router)
include_project_router(cases_router.router)
include_project_router(assessments_router.router)
include_project_router(goals_router.router)
include_project_router(care_plans_router.router)
include_project_router(schedules_router.router)
include_project_router(users_router.router)
include_project_router(reviews_router.router)
include_project_router(contact_records_router.router)
include_project_router(exports_router.router)
include_project_router(complaints_router.router)
include_project_router(leave_calc_router.router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    apply_compatible_schema_updates()
    db = SessionLocal()
    try:
        seed_goal_templates_if_empty(db)
    finally:
        db.close()


@app.exception_handler(HTTPException)
async def auth_redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return RedirectResponse(url="/login", status_code=302)
    return await http_exception_handler(request, exc)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def home(user: User = Depends(get_current_user)):
    if user.role == UserRole.caregiver:
        return RedirectResponse(url="/complaints", status_code=302)
    return RedirectResponse(url="/cases", status_code=302)


