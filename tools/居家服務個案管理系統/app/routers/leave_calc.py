import os
import re
import subprocess
import glob as glob_mod
import sys
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_roles
from app.models.user import User, UserRole

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

BASE_DIR = r"C:\Users\USER\iCloudDrive\已完成備用\汐耆\汐B\汐耆每月薪資"

COLUMNS = [
    "員工姓名", "職稱", "所屬單位", "到職日", "屆滿周年數", "結算區間",
    "結算特休天數", "過去12月實際工時(小時)", "工時比例(vs全時2088H)",
    "應享特休時數", "未休特休時數", "時薪(元)", "特休未休金(元)", "備註",
]

COL_LABELS = {
    "員工姓名": "姓名", "職稱": "職稱", "所屬單位": "單位",
    "到職日": "到職日", "屆滿周年數": "周年", "結算區間": "結算區間",
    "結算特休天數": "天數", "過去12月實際工時(小時)": "工時",
    "工時比例(vs全時2088H)": "比例", "應享特休時數": "應享",
    "未休特休時數": "未休", "時薪(元)": "時薪",
    "特休未休金(元)": "未休金", "備註": "備註",
}


def _scan_calc_files() -> dict[str, str]:
    pattern = os.path.join(BASE_DIR, "**", "特休未休金計算.xlsx")
    files = {}
    for path in glob_mod.iglob(pattern, recursive=True):
        rel = os.path.relpath(path, BASE_DIR)
        parts = rel.replace("\\", "/").split("/")
        if len(parts) >= 2:
            month_folder = parts[-2]
            m = re.fullmatch(r"(\d{3})(\d{2})", month_folder)
            if m:
                yyyymm = f"{int(m.group(1)) + 1911}-{m.group(2)}"
                files[yyyymm] = path
    return dict(sorted(files.items(), reverse=True))


def _month_display(yyyymm: str) -> str:
    y, m = yyyymm.split("-")
    return f"{int(y) - 1911}年{m}月"


def _read_calc_data(path: str) -> list[dict]:
    df = pd.read_excel(path, sheet_name="特休未休金")
    records = []
    for _, row in df.iterrows():
        rec = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                rec[col] = None
            elif isinstance(val, pd.Timestamp):
                rec[col] = val.strftime("%Y/%m/%d")
            elif isinstance(val, float):
                if val == int(val):
                    rec[col] = int(val)
                else:
                    rec[col] = round(val, 2)
            else:
                rec[col] = val
        records.append(rec)
    return records


@router.get("/leave-calc", response_class=HTMLResponse)
def leave_calc_index(
    request: Request,
    error: str | None = None,
    user: User = Depends(require_roles(UserRole.director, UserRole.accountant)),
):
    return leave_calc_view(request, None, error, user)


@router.get("/leave-calc/{yyyymm}", response_class=HTMLResponse)
def leave_calc_view(
    request: Request,
    yyyymm: str | None,
    error: str | None = None,
    user: User = Depends(require_roles(UserRole.director, UserRole.accountant)),
):
    files = _scan_calc_files()
    if yyyymm and yyyymm not in files:
        error = f"找不到 {_month_display(yyyymm)} 的計算結果"
        yyyymm = None
    if not yyyymm:
        yyyymm = next(iter(files.keys())) if files else None
    data = _read_calc_data(files[yyyymm]) if yyyymm else None
    resigned = _read_resigned_list() if user.role == UserRole.director else None
    return templates.TemplateResponse(
        request, "leave_calc_list.html", {
            "files": files, "current_month": yyyymm,
            "month_display": _month_display(yyyymm) if yyyymm else None,
            "data": data, "col_labels": COL_LABELS, "cols": COLUMNS,
            "error": error, "user": user, "resigned": resigned,
        },
    )


@router.post("/leave-calc/run")
def leave_calc_run(
    user: User = Depends(require_roles(UserRole.director)),
):
    script = os.path.join(BASE_DIR, "run_monthly.py")
    if not os.path.exists(script):
        return RedirectResponse(url="/leave-calc?error=找不到計算腳本", status_code=302)
    try:
        result = subprocess.run(
            ["python", script],
            capture_output=True, text=True, timeout=600, cwd=BASE_DIR,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "執行失敗")[:300]
            return RedirectResponse(url=f"/leave-calc?error={msg}", status_code=302)
    except subprocess.TimeoutExpired:
        return RedirectResponse(url="/leave-calc?error=計算逾時", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/leave-calc?error={str(e)[:300]}", status_code=302)
    # 計算的是上個月（run_monthly 邏輯）
    report_month = date.today().replace(day=1) - relativedelta(months=1)
    report_yyyymm = report_month.strftime("%Y-%m")
    return RedirectResponse(url=f"/leave-calc/{report_yyyymm}", status_code=302)


# ── 離職人員清單管理 ──────────────────────────────────────────────────────

RESIGNED_XLSX = os.path.join(BASE_DIR, "離職人員特休計算清單.xlsx")
RESIGNED_HEADERS = ["姓名", "到職日", "離職日", "職稱", "所屬單位", "未休特休時數", "備註"]


def _read_resigned_list() -> list[dict]:
    if not os.path.exists(RESIGNED_XLSX):
        return []
    df = pd.read_excel(RESIGNED_XLSX, dtype={"未休特休時數": float})
    records = []
    for _, row in df.iterrows():
        rec = {}
        for col in RESIGNED_HEADERS:
            val = row.get(col)
            if pd.isna(val):
                rec[col] = None
            elif isinstance(val, pd.Timestamp):
                rec[col] = val.strftime("%Y/%m/%d")
            elif isinstance(val, float) and val == int(val):
                rec[col] = int(val)
            elif isinstance(val, float):
                rec[col] = round(val, 2)
            else:
                rec[col] = val
        records.append(rec)
    return records


def _write_resigned_list(records: list[dict]):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "離職人員"
    ws.append(RESIGNED_HEADERS)
    for rec in records:
        ws.append([rec.get(h) for h in RESIGNED_HEADERS])
    wb.save(RESIGNED_XLSX)


@router.get("/leave-calc/resigned/list")
def resigned_list_api(
    user: User = Depends(require_roles(UserRole.director)),
):
    return _read_resigned_list()


@router.post("/leave-calc/resigned/add")
def resigned_add(
    name: str = Form(""),
    hire_date: str = Form(""),
    resign_date: str = Form(""),
    job_title: str = Form(""),
    unit: str = Form(""),
    unused_hours: str = Form(""),
    user: User = Depends(require_roles(UserRole.director)),
):
    if not name or not hire_date or not resign_date:
        return RedirectResponse(url="/leave-calc?error=姓名、到職日、離職日為必填", status_code=302)
    records = _read_resigned_list()
    records.append({
        "姓名": name.strip(),
        "到職日": hire_date.strip(),
        "離職日": resign_date.strip(),
        "職稱": job_title.strip() or None,
        "所屬單位": unit.strip() or None,
        "未休特休時數": float(unused_hours) if unused_hours.strip() else None,
        "備註": None,
    })
    _write_resigned_list(records)
    return RedirectResponse(url="/leave-calc", status_code=302)


@router.post("/leave-calc/resigned/delete")
def resigned_delete(
    idx: int = Form(0),
    user: User = Depends(require_roles(UserRole.director)),
):
    records = _read_resigned_list()
    if 0 <= idx < len(records):
        records.pop(idx)
        _write_resigned_list(records)
    return RedirectResponse(url="/leave-calc", status_code=302)
