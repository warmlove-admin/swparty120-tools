import calendar
from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.database import get_db
from app.models.user import User, UserRole
from app.models.monthly_salary import MonthlySalary
from app.services.insurance import (
    calc_labor_insurance_employer_pay,
    calc_health_insurance_employer_pay,
    calc_labor_pension_employer_pay,
    calc_occupational_injury_pay,
    calc_employer_monthly_cost,
)

router = APIRouter(prefix="/company")
_jinja_env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)


@router.get("", response_class=HTMLResponse)
def company_page(
    request: Request,
    year: int = 0,
    user: User = Depends(require_roles("主管", "主任", "會計")),
    db: Session = Depends(get_db),
):
    if year <= 0:
        year = date.today().year

    # All active caregivers
    caregivers = (db.query(User)
                  .filter(User.is_active.is_(True), User.role == UserRole.caregiver)
                  .order_by(User.employee_no).all())

    # Monthly employer cost per employee
    monthly_data = []  # [{caregiver, month, costs: {...}}]
    yearly_totals = {
        "labor_insurance": 0,
        "occupational_injury": 0,
        "labor_pension": 0,
        "health_insurance": 0,
        "total": 0,
    }

    for month in range(1, 13):
        month_total = 0
        month_rows = []
        for cg in caregivers:
            # Get MonthlySalary for this employee/month
            ms = db.query(MonthlySalary).filter(
                MonthlySalary.caregiver_id == cg.id,
                MonthlySalary.year == year,
                MonthlySalary.month == month,
            ).first()

            # Calculate employer costs from current insurance grades
            costs = calc_employer_monthly_cost(
                labor_grade=cg.insurance_labor_amount or 0,
                occupational_grade=cg.insurance_occupational_amount or 0,
                labor_pension_grade=cg.insurance_labor_pension_amount or 0,
                labor_pension_employer_rate=cg.labor_pension_employer_rate or 6,
                health_grade=cg.insurance_health_amount or 0,
                health_dependents=cg.health_dependents or 0,
            )

            # Income tax from monthly salary record (placeholder for future)
            income_tax = 0

            row = {
                "caregiver": cg,
                "costs": costs,
                "income_tax": income_tax,
                "total": costs["total_employer_cost"] + income_tax,
            }
            month_rows.append(row)
            month_total += row["total"]

            # Accumulate yearly
            yearly_totals["labor_insurance"] += costs["labor_insurance_employer"]
            yearly_totals["occupational_injury"] += costs["occupational_injury_employer"]
            yearly_totals["labor_pension"] += costs["labor_pension_employer"]
            yearly_totals["health_insurance"] += costs["health_insurance_employer"]
            yearly_totals["total"] += row["total"]

        monthly_data.append({
            "month": month,
            "label": f"{month}月",
            "rows": month_rows,
            "total": month_total,
            "subtotals": {
                "labor_insurance": sum(r["costs"]["labor_insurance_employer"] for r in month_rows),
                "occupational_injury": sum(r["costs"]["occupational_injury_employer"] for r in month_rows),
                "labor_pension": sum(r["costs"]["labor_pension_employer"] for r in month_rows),
                "health_insurance": sum(r["costs"]["health_insurance_employer"] for r in month_rows),
                "income_tax": sum(r["income_tax"] for r in month_rows),
            },
        })

    # Year options (current year and previous 2)
    current_year = date.today().year
    year_options = [current_year - i for i in range(3)]

    template = _jinja_env.get_template("company.html")
    html = template.render(
        user=user,
        caregivers=caregivers,
        monthly_data=monthly_data,
        yearly_totals=yearly_totals,
        selected_year=year,
        year_options=year_options,
    )
    return HTMLResponse(content=html)
