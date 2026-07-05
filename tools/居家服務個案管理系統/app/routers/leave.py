from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models.leave import LeaveType, LeaveRequest, LeaveRequestStatus
from app.models.user import User, UserRole

router = APIRouter(prefix="/leave")
templates = Jinja2Templates(directory="app/templates")


def _seed_default_leave_types(db: Session):
    """Seed default leave types if empty"""
    if db.query(LeaveType).count() > 0:
        return
    defaults = [
        ("特休", "annual", 1),
        ("事假", "personal", 2),
        ("病假", "sick", 3),
        ("喪假", "funeral", 4),
        ("公假", "official", 5),
        ("婚假", "marriage", 6),
        ("產假", "maternity", 7),
        ("陪產假", "paternity", 8),
        ("家庭照顧假", "family_care", 9),
    ]
    for name, code, order in defaults:
        db.add(LeaveType(name=name, code=code, sort_order=order))
    db.commit()


@router.get("", response_class=HTMLResponse)
def leave_page(
    request: Request,
    status_filter: str = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _seed_default_leave_types(db)

    today = date.today()
    caregivers = (
        db.query(User)
        .filter(User.role == UserRole.caregiver)
        .order_by(User.display_name)
        .all()
    )
    leave_types = (
        db.query(LeaveType)
        .filter(LeaveType.is_active == True)
        .order_by(LeaveType.sort_order)
        .all()
    )

    query = db.query(LeaveRequest).join(User, LeaveRequest.caregiver_id == User.id)
    if user.role == UserRole.caregiver:
        query = query.filter(LeaveRequest.caregiver_id == user.id)
    if status_filter:
        query = query.filter(LeaveRequest.status == LeaveRequestStatus(status_filter))
    query = query.order_by(LeaveRequest.created_at.desc())

    requests = query.all()

    # Annual leave balance
    annual_leave_type = db.query(LeaveType).filter(LeaveType.code == "annual").first()
    annual_balance = {}
    if annual_leave_type:
        for cg in caregivers:
            used = (
                db.query(func.coalesce(func.sum(LeaveRequest.days), 0))
                .filter(
                    LeaveRequest.caregiver_id == cg.id,
                    LeaveRequest.leave_type_id == annual_leave_type.id,
                    LeaveRequest.status == LeaveRequestStatus.approved,
                    LeaveRequest.start_date >= date(today.year, 1, 1),
                )
                .scalar()
            )
            annual_balance[cg.id] = float(used)

    return templates.TemplateResponse(
        request,
        "leave.html",
        {
            "user": user,
            "caregivers": caregivers,
            "leave_types": leave_types,
            "requests": requests,
            "status_filter": status_filter,
            "today": today,
            "annual_balance": annual_balance,
            "LeaveRequestStatus": LeaveRequestStatus,
            "UserRole": UserRole,
        },
    )


@router.post("/create")
def create_leave_request(
    request: Request,
    caregiver_id: str = Form(...),
    leave_type_id: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    days: float = Form(...),
    reason: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in (UserRole.director, UserRole.caregiver):
        return RedirectResponse(url="/leave?error=無權限", status_code=302)
    if user.role == UserRole.caregiver and user.id != caregiver_id:
        return RedirectResponse(url="/leave?error=只能為自己申請", status_code=302)

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/leave?error=日期格式錯誤", status_code=302)

    if days <= 0 or sd > ed:
        return RedirectResponse(url="/leave?error=日期或天數不正確", status_code=302)

    rec = LeaveRequest(
        caregiver_id=caregiver_id,
        leave_type_id=leave_type_id,
        start_date=sd,
        end_date=ed,
        days=days,
        reason=reason,
        status=LeaveRequestStatus.pending,
    )
    db.add(rec)
    db.commit()
    return RedirectResponse(url="/leave?success=請假申請已送出", status_code=302)


@router.post("/{req_id}/approve")
def approve_leave(
    req_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director)),
):
    rec = db.query(LeaveRequest).filter(LeaveRequest.id == req_id).first()
    if not rec:
        return RedirectResponse(url="/leave?error=找不到該申請", status_code=302)
    if rec.status != LeaveRequestStatus.pending:
        return RedirectResponse(url="/leave?error=只能審核待審核的申請", status_code=302)
    rec.status = LeaveRequestStatus.approved
    rec.reviewed_by = user.id
    rec.reviewed_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/leave?success=已核准", status_code=302)


@router.post("/{req_id}/reject")
def reject_leave(
    req_id: str,
    rejection_reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director)),
):
    rec = db.query(LeaveRequest).filter(LeaveRequest.id == req_id).first()
    if not rec:
        return RedirectResponse(url="/leave?error=找不到該申請", status_code=302)
    if rec.status != LeaveRequestStatus.pending:
        return RedirectResponse(url="/leave?error=只能審核待審核的申請", status_code=302)
    rec.status = LeaveRequestStatus.rejected
    rec.reviewed_by = user.id
    rec.reviewed_at = datetime.utcnow()
    rec.rejection_reason = rejection_reason
    db.commit()
    return RedirectResponse(url="/leave?success=已駁回", status_code=302)


@router.post("/leave-types/create")
def create_leave_type(
    name: str = Form(...),
    code: str = Form(...),
    sort_order: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director)),
):
    existing = db.query(LeaveType).filter(
        (LeaveType.code == code) | (LeaveType.name == name)
    ).first()
    if existing:
        return RedirectResponse(url="/leave?error=假別名稱或代碼已存在", status_code=302)
    db.add(LeaveType(name=name, code=code, sort_order=sort_order))
    db.commit()
    return RedirectResponse(url="/leave?success=假別已新增", status_code=302)


@router.post("/leave-types/{type_id}/delete")
def delete_leave_type(
    type_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.director)),
):
    lt = db.query(LeaveType).filter(LeaveType.id == type_id).first()
    if not lt:
        return RedirectResponse(url="/leave?error=找不到該假別", status_code=302)
    in_use = db.query(LeaveRequest).filter(LeaveRequest.leave_type_id == type_id).first()
    if in_use:
        lt.is_active = False
    else:
        db.delete(lt)
    db.commit()
    return RedirectResponse(url="/leave?success=假別已刪除", status_code=302)
