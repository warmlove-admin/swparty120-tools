from datetime import date, datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.database import get_db
from app.models.user import User, UserRole

router = APIRouter(prefix="/salary")


@router.get("", response_class=RedirectResponse)
def salary_index(
    request: Request,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director, UserRole.accountant)),
):
    url = "/transport-salary"
    if month:
        url += f"?month={month}"
    return RedirectResponse(url=url, status_code=302)
