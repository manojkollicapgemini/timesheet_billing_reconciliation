from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    Body,
    Depends,
    Form,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from pathlib import Path
import pandas as pd
import io
import math
import calendar
import datetime

from .db import SessionLocal, init_db, ReconEntry, CGDaily, CITIDaily
from .llm_client import call_llm, API_KEY, DEFAULT_MODEL, SYSTEM_PROMPT
from .sample_data import ensure_sample_workbook

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CG × Citi — Timesheet, Billing & Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key="cg-citi-portfolio-secret",
    session_cookie="cg_citi_session",
)

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


# ----------------- DB session -----------------


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    init_db()


# ----------------- Helper functions -----------------


def is_weekend(dt: datetime.date) -> bool:
    return dt.weekday() >= 5


def expected_hours_for_month(year: int, month: int, holidays_csv: str | None) -> float:
    holidays: set[datetime.date] = set()
    if holidays_csv:
        for tok in str(holidays_csv).split(","):
            tok = tok.strip()
            if tok:
                try:
                    holidays.add(datetime.date.fromisoformat(tok))
                except Exception:
                    pass
    _, last = calendar.monthrange(year, month)
    hours = 0
    for d in range(1, last + 1):
        day = datetime.date(year, month, d)
        if is_weekend(day) or day in holidays:
            continue
        hours += 8
    return float(hours)


def status_from(total: float, submitted: float) -> str:
    total = float(total or 0)
    submitted = float(submitted or 0)
    if submitted <= 0:
        return "Not Completed"
    if submitted < total:
        return "Partial"
    return "Completed"


def sanitize(obj):
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def month_to_range(ym: str):
    year, month = map(int, ym.split("-"))
    last = calendar.monthrange(year, month)[1]
    start = datetime.date(year, month, 1)
    end = datetime.date(year, month, last)
    return start, end


def choose_col(row, *names, default=""):
    for n in names:
        if n in row and not pd.isna(row[n]):
            return row[n]
    return default


def choose_project_code(row):
    candidates = [
        "Project Code_cg",
        "Project Code_citi",
        "Project Code",
        "ProjectCode_cg",
        "ProjectCode_citi",
        "ProjectCode",
        "Proj Code_cg",
        "Proj Code_citi",
        "Proj Code",
        "Project_cg",
        "Project_citi",
        "Project",
    ]
    for key in candidates:
        if key in row and not pd.isna(row[key]):
            val = str(row[key]).strip()
            if val:
                return val
    return "UNKNOWN"


# ----------------- Workbook ingestion -----------------


def ingest_workbook(content: bytes, db: Session):
    # Read Excel
    try:
        xls = pd.ExcelFile(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f"Invalid Excel: {e}")

    required_sheets = ["CG", "CITI", "CG_DAILY", "CITI_DAILY"]
    for sheet in required_sheets:
        if sheet not in xls.sheet_names:
            raise HTTPException(
                400, f"Workbook must include sheets: {', '.join(required_sheets)}"
            )

    df_cg = pd.read_excel(xls, sheet_name="CG")
    df_citi = pd.read_excel(xls, sheet_name="CITI")
    df_cg.columns = [c.strip() for c in df_cg.columns]
    df_citi.columns = [c.strip() for c in df_citi.columns]

    if "Citi Email" not in df_cg.columns or "Citi Email" not in df_citi.columns:
        raise HTTPException(400, "Both CG and CITI sheets must include 'Citi Email'")

    if "Month" not in df_cg.columns:
        df_cg["Month"] = None
    if "Month" not in df_citi.columns:
        df_citi["Month"] = None

    merged = pd.merge(
        df_cg,
        df_citi,
        on=["Citi Email", "Month"],
        how="outer",
        suffixes=("_cg", "_citi"),
    )

    # Clear existing ReconEntry for months in this workbook
    months_in_file = set(str(m) for m in merged["Month"].dropna().astype(str).tolist())
    for ym in months_in_file:
        db.execute(delete(ReconEntry).where(ReconEntry.month == ym))

    # Upsert ReconEntry
    for _, row in merged.iterrows():
        month_str = str(row.get("Month") or "")
        if not month_str or month_str == "nan":
            continue
        try:
            year, mon = map(int, month_str.split("-"))
        except Exception:
            continue

        eid = str(choose_col(row, "ID_cg", "ID_citi", "ID") or "")
        name = str(choose_col(row, "Name_cg", "Name_citi", "Name") or "")
        cg_email = str(choose_col(row, "CG Email_cg", "CG Email") or "")
        citi_email = str(
            choose_col(row, "Citi Email", "Citi Email_cg", "Citi Email_citi") or ""
        )
        region_code = str(
            choose_col(row, "Region Code_cg", "Region Code_citi", "Region Code") or ""
        )
        region_name = str(
            choose_col(row, "Region Name_cg", "Region Name_citi", "Region Name") or ""
        )
        project_name = str(
            choose_col(row, "Project Name_cg", "Project Name_citi", "Project Name")
            or ""
        )
        project_code = choose_project_code(row)
        billing_rate = float(
            choose_col(row, "Billing Rate_cg", "Billing Rate_citi", "Billing Rate")
            or 0
        )

        total_cg = float(row.get("Total Hours(CG)") or 0)
        submitted_cg = float(row.get("Submitted Hours(CG)") or 0)
        submitted_on_cg = row.get("Submitted On")
        if pd.isna(submitted_on_cg):
            submitted_on_cg = None

        total_citi = float(row.get("Total Hours(Citi)") or 0)
        submitted_citi = float(row.get("Submitted Hours(Citi)") or 0)
        holidays_csv = str(row.get("Holidays") or "")

        expected = expected_hours_for_month(year, mon, holidays_csv or None)
        status_cg = status_from(total_cg or expected, submitted_cg)
        status_citi = status_from(total_citi or expected, submitted_citi)
        reconciled_hours = min(submitted_cg, submitted_citi)
        diff = abs(submitted_cg - submitted_citi)

        if submitted_cg == 0 and submitted_citi == 0:
            reconciled_status = "Not Completed"
        elif diff > 0.01:
            reconciled_status = "Mismatch"
        elif status_cg == "Completed" and status_citi == "Completed":
            reconciled_status = "Completed"
        else:
            reconciled_status = "Partial"

        db.add(
            ReconEntry(
                employee_id=eid,
                month=month_str,
                name=name,
                cg_email=cg_email,
                citi_email=citi_email,
                region_code=region_code,
                region_name=region_name,
                project_name=project_name,
                project_code=project_code,
                billing_rate=billing_rate,
                total_hours_cg=total_cg,
                submitted_hours_cg=submitted_cg,
                submitted_on_cg=str(submitted_on_cg) if submitted_on_cg else None,
                status_cg=status_cg,
                total_hours_citi=total_citi,
                submitted_hours_citi=submitted_citi,
                holidays=holidays_csv or None,
                status_citi=status_citi,
                expected_hours=expected,
                reconciled_hours=reconciled_hours,
                reconciled_status=reconciled_status,
                reminders=0,
            )
        )
    db.commit()

    # Daily sheets
    df_cg_d = pd.read_excel(xls, sheet_name="CG_DAILY")
    df_ci_d = pd.read_excel(xls, sheet_name="CITI_DAILY")
    df_cg_d.columns = [c.strip() for c in df_cg_d.columns]
    df_ci_d.columns = [c.strip() for c in df_ci_d.columns]

    def ym_of(val):
        try:
            d = pd.to_datetime(val).date()
            return f"{d.year:04d}-{d.month:02d}"
        except Exception:
            return None

    months_daily = set(
        filter(
            None,
            df_cg_d["Date"].map(ym_of).tolist()
            + df_ci_d["Date"].map(ym_of).tolist(),
        )
    )

    for ym in months_daily:
        start, end = month_to_range(ym)
        db.execute(
            delete(CGDaily).where(CGDaily.date >= start, CGDaily.date <= end)
        )
        db.execute(
            delete(CITIDaily).where(CITIDaily.date >= start, CITIDaily.date <= end)
        )

    def extract_pcode(row):
        for key in ["Project Code", "ProjectCode", "Proj Code", "Project"]:
            if key in row and not pd.isna(row[key]):
                val = str(row[key]).strip()
                if val:
                    return val
        return "UNKNOWN"

    for _, r in df_cg_d.iterrows():
        try:
            db.add(
                CGDaily(
                    citi_email=str(r["Citi Email"]),
                    date=pd.to_datetime(r["Date"]).date(),
                    hours=float(r.get("Hours", 0) or 0),
                    project_code=extract_pcode(r),
                )
            )
        except Exception:
            pass

    for _, r in df_ci_d.iterrows():
        try:
            db.add(
                CITIDaily(
                    citi_email=str(r["Citi Email"]),
                    date=pd.to_datetime(r["Date"]).date(),
                    hours=float(r.get("Hours", 0) or 0),
                    project_code=extract_pcode(r),
                )
            )
        except Exception:
            pass

    db.commit()


# ----------------- Auth helpers -----------------


def is_logged_in(request: Request) -> bool:
    return request.session.get("user") == "admin"


def require_login(request: Request):
    if not is_logged_in(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )


# ----------------- HTML pages -----------------


@app.get("/", response_class=HTMLResponse)
def timesheets_page():
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return (APP_DIR / "static" / "billing.html").read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return (APP_DIR / "static" / "login.html").read_text(encoding="utf-8")


@app.post("/login")
async def login(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    if username == "admin" and password == "password":
        request.session["user"] = "admin"
        return RedirectResponse("/billing", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/login?error=1", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return (APP_DIR / "static" / "admin.html").read_text(encoding="utf-8")


@app.get("/chatbot", response_class=HTMLResponse)
def chatbot_page():
    return (APP_DIR / "static" / "chatbot.html").read_text(encoding="utf-8")


# ----------------- Admin grid upload -----------------


@app.post("/api/admin/upload-monthly-grid")
async def upload_monthly_grid(
    system: str = Form(...),
    month: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if system not in ("CG", "CITI"):
        raise HTTPException(400, "system must be 'CG' or 'CITI'")

    content = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f"Invalid Excel: {e}")

    df.columns = [str(c).strip() for c in df.columns]
    if "Citi Email" not in df.columns or "Project Code" not in df.columns:
        raise HTTPException(400, "Grid must include 'Citi Email' and 'Project Code'")

    try:
        start, end = month_to_range(month)
    except Exception:
        raise HTTPException(400, "Month must be YYYY-MM")

    day_cols = [c for c in df.columns if str(c).isdigit() and 1 <= int(c) <= 31]
    if not day_cols:
        raise HTTPException(400, "No day columns (1..31) found")

    if system == "CG":
        db.execute(
            delete(CGDaily).where(CGDaily.date >= start, CGDaily.date <= end)
        )
    else:
        db.execute(
            delete(CITIDaily).where(CITIDaily.date >= start, CITIDaily.date <= end)
        )

    y, m = start.year, start.month
    _, last = calendar.monthrange(y, m)

    for _, row in df.iterrows():
        email = str(row["Citi Email"])
        pcode = str(row["Project Code"] or "UNKNOWN")
        for dcol in day_cols:
            d = int(dcol)
            if d > last:
                continue
            hrs = float(row.get(dcol) or 0)
            if hrs <= 0:
                continue
            dte = datetime.date(y, m, d)
            if system == "CG":
                db.add(CGDaily(citi_email=email, date=dte, hours=hrs, project_code=pcode))
            else:
                db.add(CITIDaily(citi_email=email, date=dte, hours=hrs, project_code=pcode))

    db.commit()
    return {"ok": True}


# ----------------- Upload & sample -----------------


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    ingest_workbook(content, db)
    return {"ok": True}


@app.post("/api/use-sample")
def use_sample(db: Session = Depends(get_db)):
    sample = DATA_DIR / "sample_workbook.xlsx"
    ensure_sample_workbook(sample)

    content = sample.read_bytes()
    ingest_workbook(content, db)

    df = pd.read_excel(io.BytesIO(content), sheet_name="CG")
    months = sorted(set(str(m) for m in df["Month"].dropna().astype(str).tolist()))
    latest = max(months) if months else None
    latest_year, latest_month = None, None
    if latest:
        latest_year, latest_month = map(int, latest.split("-"))

    return {
        "ok": True,
        "months": months,
        "latest_year": latest_year,
        "latest_month": latest_month,
    }


# ----------------- Reporting (timesheets) -----------------


@app.get("/api/projects")
def projects(year: int, month: int, db: Session = Depends(get_db)):
    ym = f"{year:04d}-{month:02d}"
    rows = db.execute(
        select(ReconEntry.project_code).where(ReconEntry.month == ym).distinct()
    ).all()
    return {"projects": [r[0] for r in rows if r[0]]}


@app.get("/api/report")
def report(year: int, month: int, db: Session = Depends(get_db)):
    ym = f"{year:04d}-{month:02d}"
    rows = db.execute(select(ReconEntry).where(ReconEntry.month == ym)).scalars().all()

    total = len(rows)
    completed = sum(1 for r in rows if r.reconciled_status == "Completed")
    partial = sum(1 for r in rows if r.reconciled_status == "Partial")
    mismatch = sum(1 for r in rows if r.reconciled_status == "Mismatch")
    not_completed = sum(1 for r in rows if r.reconciled_status == "Not Completed")

    records = [
        {
            "employee_id": r.employee_id,
            "name": r.name,
            "email": r.cg_email or r.citi_email,
            "citi_email": r.citi_email,
            "project_code": r.project_code,
            "total_hours": max(
                r.total_hours_cg or 0, r.total_hours_citi or 0, r.expected_hours or 0
            ),
            "submitted_hours_cg": r.submitted_hours_cg,
            "submitted_hours_citi": r.submitted_hours_citi,
            "submitted_on": r.submitted_on_cg,
            "status_cg": r.status_cg,
            "status_citi": r.status_citi,
            "reconciled_status": r.reconciled_status,
            "reconciled_hours": r.reconciled_hours,
            "project": r.project_name,
            "region": r.region_name,
            "reminders": r.reminders,
        }
        for r in rows
    ]

    return JSONResponse(
        sanitize(
            {
                "year": year,
                "month": month,
                "summary": {
                    "total": total,
                    "completed": completed,
                    "partial": partial,
                    "mismatch": mismatch,
                    "not_completed": not_completed,
                },
                "records": records,
            }
        )
    )


# ----------------- Billing (secured) -----------------


@app.get("/api/billing")
def billing(
    request: Request,
    year: int,
    month: int,
    project_code: str | None = None,
    db: Session = Depends(get_db),
):
    require_login(request)

    ym = f"{year:04d}-{month:02d}"
    q = select(ReconEntry).where(ReconEntry.month == ym)
    if project_code:
        q = q.where(ReconEntry.project_code == project_code)
    current_rows = db.execute(q).scalars().all()

    per_project: dict[str, float] = {}
    for r in current_rows:
        pc = r.project_code or "UNKNOWN"
        amount = (r.reconciled_hours or 0) * (r.billing_rate or 0)
        per_project[pc] = per_project.get(pc, 0.0) + amount

    detail = [
        {
            "name": r.name,
            "email": r.cg_email or r.citi_email,
            "project_code": r.project_code,
            "reconciled_hours": r.reconciled_hours,
            "rate": r.billing_rate,
            "billing": round(
                (r.reconciled_hours or 0) * (r.billing_rate or 0), 2
            ),
        }
        for r in current_rows
    ]

    monthly_total = round(sum(per_project.values()), 2)

    # Regression-based annual projection
    all_months = sorted(set(db.execute(select(ReconEntry.month)).scalars().all()))
    month_totals = []
    for ym_str in all_months:
        q2 = select(ReconEntry).where(ReconEntry.month == ym_str)
        if project_code:
            q2 = q2.where(ReconEntry.project_code == project_code)
        rows2 = db.execute(q2).scalars().all()
        total2 = sum(
            (r.reconciled_hours or 0) * (r.billing_rate or 0) for r in rows2
        )
        month_totals.append((ym_str, total2))

    trend_labels = [m for m, _ in month_totals]
    trend_values = [t for _, t in month_totals]

    annual_projection = monthly_total * 12
    if len(month_totals) >= 2:
        xs = list(range(len(month_totals)))
        ys = trend_values
        n = len(xs)
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        denom = sum((x - x_mean) ** 2 for x in xs)
        if denom > 0:
            slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom
            intercept = y_mean - slope * x_mean
            forecasts = [max(intercept + slope * (len(xs) + i), 0.0) for i in range(12)]
            annual_projection = round(sum(forecasts), 2)
        else:
            annual_projection = round(monthly_total * 12, 2)
    else:
        annual_projection = round(monthly_total * 12, 2)

    return JSONResponse(
        sanitize(
            {
                "per_project": per_project,
                "detail": detail,
                "monthly_total": monthly_total,
                "annual_projection": annual_projection,
                "trend_labels": trend_labels,
                "trend_values": trend_values,
            }
        )
    )


# ----------------- Daily & reminders -----------------


@app.get("/api/daily")
def daily(citi_email: str, year: int, month: int, db: Session = Depends(get_db)):
    ym = f"{year:04d}-{month:02d}"
    start, end = month_to_range(ym)

    cg_rows = db.execute(
        select(CGDaily).where(
            CGDaily.citi_email == citi_email,
            CGDaily.date >= start,
            CGDaily.date <= end,
        )
    ).scalars().all()

    ci_rows = db.execute(
        select(CITIDaily).where(
            CITIDaily.citi_email == citi_email,
            CITIDaily.date >= start,
            CITIDaily.date <= end,
        )
    ).scalars().all()

    def to_map(rows):
        return {r.date.isoformat(): r.hours for r in rows}

    cg_map = to_map(cg_rows)
    ci_map = to_map(ci_rows)

    _, last = calendar.monthrange(year, month)
    days = [datetime.date(year, month, d).isoformat() for d in range(1, last + 1)]

    items = []
    for d in days:
        h_cg = cg_map.get(d, 0.0)
        h_ci = ci_map.get(d, 0.0)
        diff = round(h_cg - h_ci, 2)
        items.append(
            {
                "date": d,
                "hours_cg": h_cg,
                "hours_citi": h_ci,
                "diff": diff,
            }
        )

    return {"citi_email": citi_email, "items": items}


def _trigger_reminders_for_month(db: Session, ym: str, employee_ids=None) -> int:
    """Shared reminder logic for REST endpoint and chatbot.
    Returns number of rows updated.
    """
    rows = db.execute(select(ReconEntry).where(ReconEntry.month == ym)).scalars().all()

    if employee_ids:
        idset = set(map(str, employee_ids))
        rows = [r for r in rows if (r.employee_id in idset or r.citi_email in idset)]
    else:
        rows = [
            r
            for r in rows
            if r.reconciled_status in ("Partial", "Mismatch", "Not Completed")
        ]

    for r in rows:
        r.reminders = (r.reminders or 0) + 1

    db.commit()
    return len(rows)


@app.post("/api/send-reminder")
def send_reminder(payload: dict = Body(...), db: Session = Depends(get_db)):
    year = int(payload.get("year"))
    month = int(payload.get("month"))
    employee_ids = payload.get("employee_ids")

    ym = f"{year:04d}-{month:02d}"
    count = _trigger_reminders_for_month(db, ym, employee_ids=employee_ids)
    return {"ok": True, "count": count}


# ----------------- Chatbot -----------------


@app.post("/api/chatbot")
def chatbot_endpoint(payload: dict = Body(...), db: Session = Depends(get_db)):
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "Query is required")

    project_code = payload.get("project_code")
    q_lower = query.lower()

    all_months = sorted(set(db.execute(select(ReconEntry.month)).scalars().all()))
    ctx_lines: list[str] = []

    # Determine "last quarter" = last 3 months with data
    if all_months:
        last_quarter_months = all_months[-3:]
    else:
        last_quarter_months = []

    # 1) Last quarter billing context (by project)
    ctx_lines.append("=== Last Quarter Monthly Billing (by project) ===")
    for ym in last_quarter_months:
        q = select(ReconEntry).where(ReconEntry.month == ym)
        if project_code:
            q = q.where(ReconEntry.project_code == project_code)
        rows = db.execute(q).scalars().all()
        per_project: dict[str, float] = {}
        for r in rows:
            pc = r.project_code or "UNKNOWN"
            val = (r.reconciled_hours or 0) * (r.billing_rate or 0)
            per_project[pc] = per_project.get(pc, 0.0) + val
        for pc, val in per_project.items():
            ctx_lines.append(f"{ym} | {pc} | billing={val:.2f}")

    # 2) Latest month status (risks: mismatches & high reminders)
    latest = max(all_months) if all_months else None
    if latest:
        q = select(ReconEntry).where(ReconEntry.month == latest)
        if project_code:
            q = q.where(ReconEntry.project_code == project_code)
        rows = db.execute(q).scalars().all()
        ctx_lines.append("=== Latest Month Status (per resource) ===")
        for r in rows[:80]:
            ctx_lines.append(
                f"{latest} | {r.name} | {r.citi_email} | proj={r.project_code} | "
                f"CG={r.status_cg} | CITI={r.status_citi} | REC={r.reconciled_status} | "
                f"hours={r.reconciled_hours} | reminders={r.reminders}"
            )

    # 3) If the user is asking to send reminders, actually trigger them
    reminders_triggered = 0
    reminder_month = latest
    if reminder_month and ("remind" in q_lower or "reminder" in q_lower):
        reminders_triggered = _trigger_reminders_for_month(db, reminder_month)
        ctx_lines.append(
            f"SYSTEM_ACTION: reminders_triggered={reminders_triggered} for month={reminder_month}"
        )

    if not ctx_lines:
        ctx_lines.append("No billing or timesheet data available.")

    context_text = "\n".join(ctx_lines)

    # Build LLM prompt
    last_quarter_str = ", ".join(last_quarter_months) if last_quarter_months else "N/A"
    reminder_hint = (
        f"Reminders have just been triggered for {reminders_triggered} resources "
        f"in {reminder_month}." if reminders_triggered else
        "No reminders were triggered in this interaction."
    )

    prompt = f"""
You are assisting a portfolio manager looking at Capgemini × Citi engagement data.

Context data:
{context_text}

User question:
{query}

Important guidance:
- "Last quarter" refers to the three latest months in the context: {last_quarter_str}.
- When the question is about "last quarter billing by project", only use those three months.
- If you see SYSTEM_ACTION lines, treat them as already executed actions by the system
  (for example reminders being sent) and acknowledge them explicitly in your answer.
- Specifically for this question: {reminder_hint}

When you answer:
- Refer to projects by code (P100, P200, etc.) and month (YYYY-MM) when relevant.
- If you cannot infer an exact number from context, describe the trend qualitatively
  (e.g. "increasing", "flat", "declining") instead of guessing exact values.
- Highlight risks like mismatched timesheets, high reminders, or low utilisation.
- Be concise and business-focused, as if talking to a senior portfolio manager.
"""

    try:
        raw = call_llm(API_KEY, prompt, model_name=DEFAULT_MODEL, system_prompt=SYSTEM_PROMPT)

        # Capgemini API often returns dict with 'content' field, sometimes 'text'/'result'
        text = None
        if isinstance(raw, dict):
            text = raw.get("content") or raw.get("text") or raw.get("result")
            # Some wrappers use {'outputs': [{'content': '...'}]} etc.
            if text is None and "outputs" in raw and isinstance(raw["outputs"], list):
                maybe = raw["outputs"][0]
                if isinstance(maybe, dict):
                    text = maybe.get("content") or maybe.get("text")
        if not text:
            text = str(raw)

        return {"answer": text, "reminders_triggered": reminders_triggered}
    except Exception as e:
        raise HTTPException(500, f"LLM call failed: {e}")
