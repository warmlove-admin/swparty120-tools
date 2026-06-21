from fastapi import FastAPI, Depends, Request, status
from fastapi.exceptions import HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse

from app.database import Base, engine, SessionLocal
from app import models  # noqa: F401  (ensures all models are registered)
from app.auth import get_current_user
from app.models.user import User
from app.routers import auth as auth_router
from app.routers import cases as cases_router
from app.routers import assessments as assessments_router
from app.routers import goals as goals_router
from app.routers import care_plans as care_plans_router
from app.services.goal_template_seed import seed_goal_templates_if_empty

app = FastAPI(title="居家服務個案管理系統")
app.include_router(auth_router.router)
app.include_router(cases_router.router)
app.include_router(assessments_router.router)
app.include_router(goals_router.router)
app.include_router(care_plans_router.router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
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
    return RedirectResponse(url="/cases", status_code=302)


