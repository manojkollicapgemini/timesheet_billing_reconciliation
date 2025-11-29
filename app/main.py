import json
import math
import calendar
import datetime
import re

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
from sqlalchemy import select, delete, or_, text
from pydantic import BaseModel
from fastapi import Body


from pathlib import Path
import pandas as pd
import io
from sqlalchemy import text


from .db import (
    SessionLocal,
    init_db,
    ReconEntry,
    CGDaily,
    CITIDaily,
    Employee,
    TimeOff,
    engine,
)
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

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def parse_month_from_query(q_lower: str) -> str | None:
    """
    Parse something like:
    - 'nov 2025'
    - 'november 2025'
    - '2025-11'
    and return 'YYYY-MM' or None.
    """
    m = re.search(
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s+(\d{4})',
        q_lower,
    )
    if m:
        mon_word = m.group(1)
        year = int(m.group(2))
        mon = MONTH_MAP.get(mon_word, 0)
        if mon:
            return f"{year:04d}-{mon:02d}"

    m = re.search(r'(\d{4})-(\d{1,2})', q_lower)
    if m:
        year = int(m.group(1))
        mon = int(m.group(2))
        if 1 <= mon <= 12:
            return f"{year:04d}-{mon:02d}"

    return None


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

def generate_sql_from_question(question: str, project_code: str | None) -> str:
    """
    Ask the LLM to produce a single safe SQL SELECT statement for SQLite
    against our schema (employees, recon_entries, cg_daily, citi_daily, time_off).
    Robust to non-perfect JSON, and post-processes common mistakes.
    """
    schema_description = """
We are using SQLite. The relevant tables are:

Table employees:
- id INTEGER PRIMARY KEY
- employee_id TEXT
- name TEXT
- cg_email TEXT
- citi_email TEXT
- region_code TEXT
- region_name TEXT
- default_project_code TEXT
- billing_rate REAL
- role TEXT
- manager TEXT
- annual_leave_allowance INTEGER
- status TEXT      -- 'Active' or 'Inactive'
- start_date DATE
- end_date DATE

Table recon_entries:
- id INTEGER PRIMARY KEY
- employee_id TEXT        -- this is a business ID, NOT a foreign key to employees.id
- month TEXT              -- 'YYYY-MM'
- name TEXT
- cg_email TEXT
- citi_email TEXT
- region_code TEXT
- region_name TEXT
- project_name TEXT
- project_code TEXT
- billing_rate REAL
- total_hours_cg REAL
- submitted_hours_cg REAL
- submitted_on_cg TEXT
- status_cg TEXT          -- 'Completed', 'Partial', 'Not Completed'
- total_hours_citi REAL
- submitted_hours_citi REAL
- holidays TEXT
- status_citi TEXT        -- same status codes
- expected_hours REAL
- reconciled_hours REAL
- reconciled_status TEXT  -- 'Completed', 'Partial', 'Mismatch', 'Not Completed'
- reminders INTEGER

Table cg_daily:
- id INTEGER PRIMARY KEY
- citi_email TEXT
- date DATE
- hours REAL
- project_code TEXT

Table citi_daily:
- id INTEGER PRIMARY KEY
- citi_email TEXT
- date DATE
- hours REAL
- project_code TEXT

Table time_off:
- id INTEGER PRIMARY KEY
- employee_id INTEGER   -- optional link to employees.id
- citi_email TEXT
- start_date DATE
- end_date DATE
- days REAL             -- working days in this time off
- leave_type TEXT       -- 'Planned', 'Sick', 'Unpaid', etc.
- reason TEXT
- status TEXT           -- 'Pending', 'Approved', 'Rejected'

VERY IMPORTANT:
- There is NO 'projects' table. Do NOT select from or join 'projects'.
- Project code and project name always come from recon_entries.project_code and recon_entries.project_name.
"""

    project_filter_hint = (
        f"\nThe UI has an optional project filter currently set to: {project_code}.\n"
        "If this is relevant, you may add a condition like `AND project_code = '<code>'`.\n"
        if project_code
        else ""
    )

    examples = """
Examples of correct queries:

1) Employees with mismatched timesheets for November 2025:
SELECT
  e.employee_id,
  e.name,
  e.cg_email,
  e.citi_email,
  r.month,
  r.reconciled_status,
  r.status_cg,
  r.status_citi
FROM employees e
JOIN recon_entries r
  ON e.citi_email = r.citi_email
WHERE r.month = '2025-11'
  AND r.reconciled_status = 'Mismatch';

2) Billing summary by project for November 2025:
SELECT
  r.project_code,
  r.project_name,
  SUM(r.submitted_hours_cg * r.billing_rate) AS cg_billing,
  SUM(r.submitted_hours_citi * r.billing_rate) AS citi_billing
FROM recon_entries r
WHERE r.month = '2025-11'
  AND r.reconciled_status = 'Completed'
GROUP BY r.project_code, r.project_name;

3) Leave usage vs allowance by employee for 2025:
SELECT
  e.employee_id,
  e.name,
  e.annual_leave_allowance,
  COALESCE(SUM(t.days), 0) AS days_taken
FROM employees e
LEFT JOIN time_off t
  ON e.id = t.employee_id
  AND t.status = 'Approved'
  AND strftime('%Y', t.start_date) = '2025'
GROUP BY e.employee_id, e.name, e.annual_leave_allowance;
"""

    prompt = f"""
You are an assistant that writes SQL queries for SQLite.

User question:
{question}

Database schema:
{schema_description}
{project_filter_hint}

Here are some example patterns:
{examples}

Important rules:
- Use ONLY the columns listed in the schema. Do NOT invent new tables or columns.
- There is NO 'projects' table. Never use 'projects' in FROM or JOIN.
- For recon_entries, there is NO 'status' column. Use:
    - reconciled_status (overall status)
    - status_cg (Capgemini timesheet status)
    - status_citi (Citi timesheet status)
- If you need to compare employee vs recon status, compare:
    - employees.status (Active/Inactive)
    - recon_entries.reconciled_status (Completed/Partial/Mismatch/Not Completed)
- If you need to join employees and recon_entries, use:
    employees.citi_email = recon_entries.citi_email
  (do NOT join on employees.id, because recon_entries.employee_id is a TEXT business ID).
- For "completed timesheets" use reconciled_status = 'Completed' in recon_entries.
- For "mismatched timesheets" use reconciled_status = 'Mismatch'.
- For leave questions, use time_off together with employees.annual_leave_allowance and SUM(time_off.days).
- The recon_entries.month column is already in 'YYYY-MM' text format.
  Do NOT wrap it in strftime(); just filter like: month = '2025-11'.

Output format:
- Return a SINGLE SQL statement.
- The statement MUST be a SELECT only. No INSERT/UPDATE/DELETE/DROP/PRAGMA etc.
- Do not use multiple statements or semicolons.

Return your answer as JSON with keys:
- "sql": the SQL SELECT statement as a string
- "comment": short explanation of what the query returns
"""

    raw = call_llm(
        API_KEY,
        prompt,
        model_name=DEFAULT_MODEL,
        system_prompt="You are a SQL generator that outputs only JSON.",
    )

    # Get raw text from Capgemini API response
    if isinstance(raw, dict):
        text_out = raw.get("content") or raw.get("text") or raw.get("result")
    else:
        text_out = str(raw)
    if text_out is None:
        raise HTTPException(500, f"SQL generator returned empty response: {raw}")

    # ---------- 1) Try strict JSON ----------
    sql_clean: str | None = None
    try:
        obj = json.loads(text_out)
        if isinstance(obj, dict) and isinstance(obj.get("sql"), str):
            sql_clean = obj["sql"]
    except Exception:
        sql_clean = None

    # ---------- 2) Fallback: regex "sql": "..." ----------
    if not sql_clean:
        m = re.search(r'"sql"\s*:\s*"(.+?)"', text_out, flags=re.DOTALL | re.IGNORECASE)
        if not m:
            m = re.search(
                r"'sql'\s*:\s*'(.+?)'", text_out, flags=re.DOTALL | re.IGNORECASE
            )
        if not m:
            raise HTTPException(
                500,
                f"SQL generator returned invalid JSON or could not find 'sql': {text_out}",
            )

        sql_raw = m.group(1)

        # 🔹 Clean typical escaping from LLM
        # Handle literal "\n" and "\t" BEFORE we strip bare backslashes.
        sql_raw = sql_raw.replace("\\n", " ")
        sql_raw = sql_raw.replace("\\t", " ")
        # Handle real newlines if any slipped through
        sql_raw = sql_raw.replace("\\\n", " ")
        sql_raw = sql_raw.replace("\n", " ")
        # Unescape quotes
        sql_raw = sql_raw.replace('\\"', '"')

        sql_clean = sql_raw

    # Trim and strip trailing semicolon/backticks
    sql_clean = sql_clean.strip().strip("`").strip().strip(";")

    # ---------- Extra cleaning for stray backslashes ----------
    sql_clean = sql_clean.replace("\\", " ")

    # ---------- Normalise whitespace & remove "n t" artefacts ----------
    # Collapse any weird whitespace sequences (tabs, multiple spaces, etc.)
    sql_clean = re.sub(r"\s+", " ", sql_clean)

    # If the model emitted "\n\t" and we stripped "\" we might get " n t ".
    # Remove standalone "n t" tokens safely.
    sql_clean = re.sub(r"\bn\s+t\b", " ", sql_clean)
    sql_clean = re.sub(r"\s+", " ", sql_clean).strip()

    # ---------- Post-processing / patch common LLM mistakes ----------

    # 1) Fix wrong join on employees.id
    sql_clean = re.sub(
        r"join\s+recon_entries\s+r\s+on\s+e\.id\s*=\s*r\.employee_id",
        "JOIN recon_entries r ON e.citi_email = r.citi_email",
        sql_clean,
        flags=re.IGNORECASE,
    )

    # 2) Fix references to non-existent re.status / recon_entries.status
    sql_clean = re.sub(
        r"\bre\.status\b", "re.reconciled_status", sql_clean, flags=re.IGNORECASE
    )
    sql_clean = re.sub(
        r"\brecon_entries\.status\b",
        "recon_entries.reconciled_status",
        sql_clean,
        flags=re.IGNORECASE,
    )

    # 3) Remove unnecessary strftime('%Y-%m', re.month) on TEXT column
    sql_clean = re.sub(
        r"strftime\(\s*'%Y-%m'\s*,\s*re\.month\s*\)",
        "re.month",
        sql_clean,
        flags=re.IGNORECASE,
    )
    sql_clean = re.sub(
        r"strftime\(\s*'%Y-%m'\s*,\s*recon_entries\.month\s*\)",
        "recon_entries.month",
        sql_clean,
        flags=re.IGNORECASE,
    )

    # 4) Fix alias 'time_off to' -> 'time_off t_off' and its usages 'to.'
    sql_clean = re.sub(
        r"\btime_off\s+to\b", "time_off t_off", sql_clean, flags=re.IGNORECASE
    )
    sql_clean = re.sub(r"\bto\.", "t_off.", sql_clean)

    # 5) Fix typo for submitted_hours_cg if LLM writes it incorrectly
    sql_clean = re.sub(
        r"\bre\.submitted_hours_cg\b",
        "re.submitted_hours_cg",
        sql_clean,
        flags=re.IGNORECASE,
    )

    # 6) Remove invented 'projects' table joins and map p.* → recon_entries.*
    #    First nuke the join clause.
    sql_clean = re.sub(
        r"\s+left\s+join\s+projects\s+p\s+on\s+.*?(?=(left join|right join|inner join|where|group by|order by|$))",
        " ",
        sql_clean,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sql_clean = re.sub(
        r"\s+join\s+projects\s+p\s+on\s+.*?(?=(left join|right join|inner join|where|group by|order by|$))",
        " ",
        sql_clean,
        flags=re.IGNORECASE | re.DOTALL,
    )

    #    Then replace p.project_name / p.project_code with recon_entries alias guesses.
    sql_clean = re.sub(
        r"\bp\.project_name\b", "rg.project_name", sql_clean, flags=re.IGNORECASE
    )
    sql_clean = re.sub(
        r"\bp\.project_code\b", "rg.project_code", sql_clean, flags=re.IGNORECASE
    )
    # Fallback if alias is 're' not 'rg'
    sql_clean = re.sub(
        r"\bre\.project_name\b", "re.project_name", sql_clean, flags=re.IGNORECASE
    )
    sql_clean = re.sub(
        r"\bre\.project_code\b", "re.project_code", sql_clean, flags=re.IGNORECASE
    )

    # ---------- Safety checks ----------

    sql_clean = sql_clean.strip()
    if not sql_clean.lower().startswith("select"):
        raise HTTPException(500, f"Generated SQL is not a SELECT: {sql_clean}")

    forbidden = [
        "insert ",
        "update ",
        "delete ",
        "drop ",
        "alter ",
        "pragma ",
        "attach ",
        "detach ",
    ]
    lower_sql = sql_clean.lower()
    if any(tok in lower_sql for tok in forbidden):
        raise HTTPException(
            500, f"Generated SQL contains forbidden operations: {sql_clean}"
        )

    return sql_clean



def run_sql_and_fetch(sql: str) -> list[dict]:
    """Execute a safe SELECT against SQLite and return list of dict rows."""
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        rows = [dict(r) for r in result.mappings().all()]
    return rows


def summarise_sql_answer(question: str, sql: str, rows: list[dict]) -> str:
    """
    Ask the LLM to turn SQL result rows into a natural-language answer.
    The model is NOT allowed to invent rows that are not in the result.
    """
    rows_preview = json.dumps(rows[:100], default=str)  # cap preview for prompt size

    prompt = f"""
You are a portfolio / delivery manager assistant.

You are given:
1) A user question.
2) An actual SQL query that was executed against the live CG × Citi database.
3) The resulting rows in JSON format (up to 100 rows shown).

You MUST base your answer *only* on the rows shown.
If the question asks to "list employees", "show the list", or similar,
you may respond with a bullet list or a compact table based only on those rows.

If there is no data for the question, say so explicitly.
Never invent people, numbers, or projects not present in the rows.

User question:
{question}

Executed SQL:
{sql}

Result rows (JSON):
{rows_preview}

Instructions:
- Be concise and business-focused.
- If listing employees, include name, emails, project_code, status, and any metrics in the rows.
- Do not guess or extrapolate beyond the given rows.
"""

    raw = call_llm(
        API_KEY,
        prompt,
        model_name=DEFAULT_MODEL,
        system_prompt="You explain SQL result sets to portfolio managers, without hallucinating.",
    )

    text_out = None
    if isinstance(raw, dict):
        text_out = raw.get("content") or raw.get("text") or raw.get("result")
        if text_out is None and "outputs" in raw and isinstance(raw["outputs"], list):
            maybe = raw["outputs"][0]
            if isinstance(maybe, dict):
                text_out = maybe.get("content") or maybe.get("text")
    if not text_out:
        text_out = str(raw)

    return text_out


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


def upsert_employee_from_row(
    db: Session,
    eid: str,
    name: str,
    cg_email: str,
    citi_email: str,
    region_code: str,
    region_name: str,
    project_code: str,
    billing_rate: float,
    year: int,
    mon: int,
):
    """Create/update Employee master record from monthly CG/CITI row."""
    if not citi_email:
        return

    emp = (
        db.execute(
            select(Employee).where(Employee.citi_email == citi_email)
        ).scalar_one_or_none()
    )

    if emp is None:
        emp = Employee(
            employee_id=eid or None,
            name=name or None,
            cg_email=cg_email or None,
            citi_email=citi_email,
            region_code=region_code or None,
            region_name=region_name or None,
            default_project_code=project_code or None,
            billing_rate=billing_rate or 0.0,
            status="Active",
            start_date=datetime.date(year, mon, 1),
        )
        db.add(emp)
    else:
        if eid:
            emp.employee_id = eid
        if name:
            emp.name = name
        if cg_email:
            emp.cg_email = cg_email
        if region_code:
            emp.region_code = region_code
        if region_name:
            emp.region_name = region_name
        if project_code and project_code != "UNKNOWN":
            emp.default_project_code = project_code
        if billing_rate:
            emp.billing_rate = billing_rate

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

    # --------- Monthly sheets (CG / CITI) ---------
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

    # Ensure uniqueness: one row per (Citi Email, Month)
    merged = merged.sort_values(by=["Citi Email", "Month"])
    merged = merged.drop_duplicates(subset=["Citi Email", "Month"], keep="last")

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

        # ---- Expected hours logic with holidays + approved time off ----
        base_expected = expected_hours_for_month(year, mon, holidays_csv or None)

        # Approved time off for this email in this month (in hours)
        timeoff_hours = approved_timeoff_hours_for_month(db, citi_email, year, mon)

        # Effective expected after subtracting approved time off
        effective_expected = max(base_expected - timeoff_hours, 0.0)

        # For status comparison, use effective expected (not raw 160)
        submitted_cg = float(submitted_cg or 0.0)
        submitted_citi = float(submitted_citi or 0.0)

        # Status per system vs effective expected
        status_cg = status_from(effective_expected, submitted_cg)
        status_citi = status_from(effective_expected, submitted_citi)

        # Reconciled hours: what we can honestly bill, capped by effective expected
        reconciled_hours = min(submitted_cg, submitted_citi, effective_expected)
        diff = abs(submitted_cg - submitted_citi)
        tol = 0.01

        if submitted_cg <= tol and submitted_citi <= tol:
            reconciled_status = "Not Completed"
        elif diff > tol:
            reconciled_status = "Mismatch"
        elif status_cg == "Completed" and status_citi == "Completed":
            reconciled_status = "Completed"
        else:
            reconciled_status = "Partial"

        # Maintain employee master from this row
        upsert_employee_from_row(
            db=db,
            eid=eid,
            name=name,
            cg_email=cg_email,
            citi_email=citi_email,
            region_code=region_code,
            region_name=region_name,
            project_code=project_code,
            billing_rate=billing_rate,
            year=year,
            mon=mon,
        )

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
                # Store EFFECTIVE expected (after time off)
                expected_hours=effective_expected,
                reconciled_hours=reconciled_hours,
                reconciled_status=reconciled_status,
                reminders=0,
            )
        )

    db.commit()

    # --------- Daily sheets (CG_DAILY / CITI_DAILY) ---------
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

    # Clear existing daily rows for months covered in this workbook
    for ym in months_daily:
        start, end = month_to_range(ym)
        db.execute(
            delete(CGDaily).where(CGDaily.date >= start, CGDaily.date <= end)
        )
        db.execute(
            delete(CITIDaily).where(CITIDaily.date >= start, CITIDaily.date <= end)
        )

    def extract_pcode(row, db_session: Session, citi_email: str, date_obj: datetime.date):
        """
        Prefer project code from sheet; if missing, fall back to monthly ReconEntry
        for this (citi_email, month). This avoids 'UNKNOWN' when we actually know it.
        """
        # 1) direct from sheet
        for key in ["Project Code", "ProjectCode", "Proj Code", "Project"]:
            if key in row and not pd.isna(row[key]):
                val = str(row[key]).strip()
                if val:
                    return val

        # 2) fallback: lookup in ReconEntry for this email + month
        ym = f"{date_obj.year:04d}-{date_obj.month:02d}"
        rec = db_session.execute(
            select(ReconEntry.project_code).where(
                ReconEntry.citi_email == citi_email,
                ReconEntry.month == ym,
            )
        ).scalar_one_or_none()
        if rec:
            return rec

        # 3) still unknown
        return "UNKNOWN"

    # Insert CG daily
    for _, r in df_cg_d.iterrows():
        try:
            c_email = str(r["Citi Email"])
            date_val = pd.to_datetime(r["Date"]).date()
            pcode = extract_pcode(r, db, c_email, date_val)
            db.add(
                CGDaily(
                    citi_email=c_email,
                    date=date_val,
                    hours=float(r.get("Hours", 0) or 0),
                    project_code=pcode,
                )
            )
        except Exception:
            # best-effort; skip malformed rows
            pass

    # Insert CITI daily
    for _, r in df_ci_d.iterrows():
        try:
            c_email = str(r["Citi Email"])
            date_val = pd.to_datetime(r["Date"]).date()
            pcode = extract_pcode(r, db, c_email, date_val)
            db.add(
                CITIDaily(
                    citi_email=c_email,
                    date=date_val,
                    hours=float(r.get("Hours", 0) or 0),
                    project_code=pcode,
                )
            )
        except Exception:
            pass

    db.commit()


def working_days_between(start: datetime.date, end: datetime.date) -> int:
    """Count working days (Mon–Fri) between start and end inclusive."""
    if end < start:
        start, end = end, start
    days = 0
    cur = start
    one = datetime.timedelta(days=1)
    while cur <= end:
        if not is_weekend(cur):
            days += 1
        cur += one
    return days

HOURS_PER_DAY = 8  # adjust if your org uses a different standard


def working_days_overlap_in_month(
    start: datetime.date, end: datetime.date, year: int, month: int
) -> int:
    """
    Number of working days where [start, end] overlaps with the given month (year, month).
    Used to pro-rate approved time off per month.
    """
    if end < start:
        start, end = end, start

    first_of_month = datetime.date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    last_of_month = datetime.date(year, month, last_day)

    # intersection of [start, end] with [first_of_month, last_of_month]
    start_clamped = max(start, first_of_month)
    end_clamped = min(end, last_of_month)
    if end_clamped < start_clamped:
        return 0

    days = 0
    cur = start_clamped
    one = datetime.timedelta(days=1)
    while cur <= end_clamped:
        if not is_weekend(cur):
            days += 1
        cur += one
    return days


def approved_timeoff_hours_for_month(
    db: Session, citi_email: str | None, year: int, month: int
) -> float:
    """
    Sum of approved time off hours for a resource in a specific month.
    Uses working-day overlap and HOURS_PER_DAY.
    """
    if not citi_email:
        return 0.0

    first_of_month = datetime.date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    last_of_month = datetime.date(year, month, last_day)

    q = select(TimeOff).where(
        TimeOff.citi_email == citi_email,
        TimeOff.status == "Approved",
        TimeOff.start_date <= last_of_month,
        TimeOff.end_date >= first_of_month,
    )
    rows = db.execute(q).scalars().all()

    total_hours = 0.0
    for t in rows:
        overlap_days = working_days_overlap_in_month(
            t.start_date, t.end_date, year, month
        )
        total_hours += overlap_days * HOURS_PER_DAY

    return total_hours



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

@app.get("/employees", response_class=HTMLResponse)
def employees_page():
    return (APP_DIR / "static" / "employees.html").read_text(encoding="utf-8")


@app.get("/timeoff", response_class=HTMLResponse)
def timeoff_page():
    return (APP_DIR / "static" / "timeoff.html").read_text(encoding="utf-8")

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

# ----------------- Time-off / leave management -----------------


@app.get("/api/timeoff")
def list_timeoff(
    year: int,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    # Filter by start_date year
    year_start = datetime.date(year, 1, 1)
    year_end = datetime.date(year, 12, 31)

    q = select(TimeOff).where(
        TimeOff.start_date >= year_start,
        TimeOff.start_date <= year_end,
    )
    if status in ("Pending", "Approved", "Rejected"):
        q = q.where(TimeOff.status == status)

    timeoffs = db.execute(q).scalars().all()

    # Map citi_email -> employee
    emps = db.execute(select(Employee)).scalars().all()
    by_email = { (e.citi_email or "").lower(): e for e in emps }

    items = []
    for t in timeoffs:
        emp = by_email.get((t.citi_email or "").lower())
        items.append(
            {
                "id": t.id,
                "employee_name": emp.name if emp else None,
                "employee_id": emp.employee_id if emp else None,
                "citi_email": t.citi_email,
                "leave_type": t.leave_type,
                "reason": t.reason,
                "start_date": t.start_date.isoformat() if t.start_date else None,
                "end_date": t.end_date.isoformat() if t.end_date else None,
                "days": t.days,
                "status": t.status,
            }
        )
    return items


@app.get("/api/timeoff/summary")
def timeoff_summary(year: int, db: Session = Depends(get_db)):
    year_start = datetime.date(year, 1, 1)
    year_end = datetime.date(year, 12, 31)

    emps = db.execute(select(Employee)).scalars().all()

    items = []
    total_approved_days = 0.0
    total_pending_requests = 0

    for e in emps:
        if not e.citi_email:
            continue

        q = select(TimeOff).where(
            TimeOff.citi_email == e.citi_email,
            TimeOff.status == "Approved",
            TimeOff.start_date >= year_start,
            TimeOff.start_date <= year_end,
        )
        approved = db.execute(q).scalars().all()
        used = sum(t.days or 0.0 for t in approved)
        total_approved_days += used

        q_pending = select(TimeOff).where(
            TimeOff.citi_email == e.citi_email,
            TimeOff.status == "Pending",
            TimeOff.start_date >= year_start,
            TimeOff.start_date <= year_end,
        )
        pending = db.execute(q_pending).scalars().all()
        total_pending_requests += len(pending)

        allowance = e.annual_leave_allowance or 15
        remaining = max(allowance - used, 0.0)

        items.append(
            {
                "employee_id": e.employee_id,
                "name": e.name,
                "citi_email": e.citi_email,
                "allowance": allowance,
                "used": used,
                "remaining": remaining,
            }
        )

    # sort by used desc for UI
    items_sorted = sorted(items, key=lambda x: x["used"], reverse=True)

    return {
        "year": year,
        "items": items_sorted,
        "total_approved_days": total_approved_days,
        "total_pending_requests": total_pending_requests,
    }


@app.post("/api/timeoff")
def create_timeoff(payload: dict = Body(...), db: Session = Depends(get_db)):
    citi_email = (payload.get("citi_email") or "").strip().lower()
    if not citi_email:
        raise HTTPException(400, "citi_email is required")

    start_str = payload.get("start_date")
    end_str = payload.get("end_date")
    if not start_str or not end_str:
        raise HTTPException(400, "start_date and end_date are required")

    try:
        start_date = datetime.date.fromisoformat(start_str)
        end_date = datetime.date.fromisoformat(end_str)
    except Exception:
        raise HTTPException(400, "Invalid date format; expected YYYY-MM-DD")

    leave_type = payload.get("leave_type") or "Planned"
    reason = payload.get("reason") or ""

    # Link employee_id if we know them
    emp = (
        db.execute(
            select(Employee).where(Employee.citi_email == citi_email)
        ).scalar_one_or_none()
    )

    days = working_days_between(start_date, end_date)

    t = TimeOff(
        employee_id=emp.id if emp else None,
        citi_email=citi_email,
        start_date=start_date,
        end_date=end_date,
        days=days,
        leave_type=leave_type,
        reason=reason,
        status="Pending",
    )
    db.add(t)
    db.commit()
    db.refresh(t)

    return {
        "id": t.id,
        "days": t.days,
        "status": t.status,
    }


@app.post("/api/timeoff/{timeoff_id}/status")
def update_timeoff_status(
    timeoff_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    status_new = (payload.get("status") or "").strip().title()
    if status_new not in ("Pending", "Approved", "Rejected"):
        raise HTTPException(400, "status must be Pending, Approved, or Rejected")

    t = db.get(TimeOff, timeoff_id)
    if not t:
        raise HTTPException(404, "Time off request not found")

    t.status = status_new
    db.commit()
    return {"ok": True}


# ----------------- Chatbot -----------------
def summarise_query_result(question: str, sql: str, rows) -> str:
    """
    Take the user question, the SQL that was run, and DB rows,
    and produce a **deterministic** English answer – no LLM here.
    """
    # rows is a list of Row objects; convert to list[dict]
    data = [dict(r._mapping) for r in rows]
    if not data:
        return (
            "I ran the following query but did not find any matching records:\n\n"
            f"`{sql}`"
        )

    # Normalise column names
    cols = {c.lower(): c for c in data[0].keys()}
    colset = set(cols.keys())

    # Helper for safe col access
    def c(name: str) -> str:
        return cols.get(name.lower(), name)

    # 1) Billing by project (most common pattern)
    if {"project_code", "project_name"}.issubset(colset) and (
        "cg_billing" in colset or "citi_billing" in colset
    ):
        lines = []
        total_cg = 0.0
        total_citi = 0.0
        for row in data:
            pcode = row.get(c("project_code"))
            pname = row.get(c("project_name"))
            cg_b = float(row.get(c("cg_billing"), 0) or 0)
            ct_b = float(row.get(c("citi_billing"), 0) or 0)
            total_cg += cg_b
            total_citi += ct_b
            lines.append(
                f"- **{pcode} – {pname}** · CG billing: {cg_b:,.2f} · Citi billing: {ct_b:,.2f}"
            )

        header = "Here is the billing summary by project based on the database values:\n\n"
        footer = (
            f"\n**Totals across all listed projects** · "
            f"CG: {total_cg:,.2f} · Citi: {total_citi:,.2f}"
        )
        return header + "\n".join(lines) + footer

    # 2) Employees with mismatched / incomplete timesheets
    if "reconciled_status" in colset and (
        "name" in colset or "employee_id" in colset or "citi_email" in colset
    ):
        lines = []
        for row in data:
            name = row.get(c("name")) or "Unknown"
            emp_id = row.get(c("employee_id"))
            email = row.get(c("citi_email")) or row.get(c("cg_email"))
            month = row.get(c("month"))
            status = row.get(c("reconciled_status"))
            scg = row.get(c("status_cg"))
            sciti = row.get(c("status_citi"))
            parts = [f"**{name}**"]
            if emp_id:
                parts.append(f"(ID {emp_id})")
            if email:
                parts.append(f"— {email}")
            meta = []
            if month:
                meta.append(f"month={month}")
            if status:
                meta.append(f"overall={status}")
            if scg:
                meta.append(f"CG={scg}")
            if sciti:
                meta.append(f"Citi={sciti}")
            line = " ".join(parts)
            if meta:
                line += " · " + ", ".join(meta)
            lines.append(f"- {line}")

        return (
            f"I found **{len(data)}** employee-timesheet records matching your criteria:\n\n"
            + "\n".join(lines)
            + "\n\n(From SQL: `" + sql + "`)"
        )

    # 3) Leave / time-off usage pattern
    if ("annual_leave_allowance" in colset and "days_taken" in colset) or (
        "total_leave" in colset
    ):
        days_col = c("days_taken") if "days_taken" in colset else c("total_leave")
        lines = []
        for row in data:
            name = row.get(c("name")) or "Unknown"
            allowance = float(row.get(c("annual_leave_allowance"), 0) or 0)
            taken = float(row.get(days_col, 0) or 0)
            remaining = max(allowance - taken, 0.0)
            lines.append(
                f"- **{name}** · used {taken:.1f} days out of {allowance:.1f}, "
                f"remaining {remaining:.1f}"
            )

        return (
            "Here is the annual leave usage by employee (based on actual DB values):\n\n"
            + "\n".join(lines)
            + "\n\n(From SQL: `" + sql + "`)"
        )

    # 4) Generic summary – do not invent domain logic
    # Show first few rows as a markdown table
    head = data[:10]
    headers = list(head[0].keys())

    table_lines = []
    # header row
    table_lines.append("| " + " | ".join(headers) + " |")
    table_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in head:
        vals = []
        for h in headers:
            v = row.get(h)
            if isinstance(v, float):
                vals.append(f"{v:.2f}")
            else:
                vals.append(str(v))
        table_lines.append("| " + " | ".join(vals) + " |")

    more_note = ""
    if len(data) > len(head):
        more_note = f"\n\n…and {len(data) - len(head)} more rows."

    return (
        "I executed the following query:\n\n"
        f"`{sql}`\n\n"
        "Here are the first rows returned:\n\n"
        + "\n".join(table_lines)
        + more_note
    )

class ChatRequest(BaseModel):
    question: str
    project_code: str | None = None


@app.post("/api/chat")
def chat_with_semantic_model(
    req: ChatRequest,
    db: Session = Depends(get_db),
):
    """
    Semantic chat endpoint:
    1) Use LLM ONLY to generate SQL.
    2) Execute SQL safely.
    3) Summarise deterministically from DB rows (no LLM touching numbers).
    """
    question = req.question.strip()
    project_code = (req.project_code or "").strip() or None

    # 1) Generate SQL via LLM
    try:
        sql = generate_sql_from_question(question, project_code)
    except HTTPException as e:
        # propagate our structured error
        raise
    except Exception as e:
        raise HTTPException(
            500, f"Error while generating SQL from question: {e}"
        ) from e

    # 2) Execute SQL safely
    try:
        result = db.execute(text(sql))
        rows = result.fetchall()
    except Exception as e:
        # Surface SQL + error to the UI, for debugging in hackathon
        raise HTTPException(
            500, f"Failed to execute generated SQL: {e}\n[SQL: {sql}]"
        ) from e

    # 3) Summarise purely from DB rows
    answer = summarise_query_result(question, sql, rows)

    return {
        "ok": True,
        "question": question,
        "sql": sql,
        "row_count": len(rows),
        "answer": answer,
    }


@app.post("/api/chatbot")
def chatbot_endpoint(payload: dict = Body(...), db: Session = Depends(get_db)):
    """
    Chatbot flow:
    1) Use LLM to generate a safe SELECT SQL for our schema.
    2) Execute the SQL on SQLite.
    3) Use LLM again to turn the *actual rows* into a natural-language answer.

    This ensures questions like
      "List me all employees whose timesheets are completed for Nov 2025"
    are answered directly from the data.
    """
    question = (payload.get("query") or "").strip()
    if not question:
        raise HTTPException(400, "Query is required")

    project_code = payload.get("project_code")

    # Step 1: generate SQL based on the question + schema
    sql = generate_sql_from_question(question, project_code)

    # Step 2: run SQL on live DB
    try:
        rows = run_sql_and_fetch(sql)
    except Exception as e:
        raise HTTPException(500, f"Failed to execute generated SQL: {e}")

    # Step 3: summarise answer from rows
    answer = summarise_sql_answer(question, sql, rows)

    # We don't automatically send reminders here; that can be a separate endpoint or flow
    return {"answer": answer, "row_count": len(rows), "sql": sql}


# ----------------- Employee master APIs -----------------


@app.get("/api/employees")
def list_employees(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Employee)
    if status in ("Active", "Inactive"):
        q = q.where(Employee.status == status)
    rows = db.execute(q).scalars().all()

    return [
        {
            "id": e.id,
            "employee_id": e.employee_id,
            "name": e.name,
            "cg_email": e.cg_email,
            "citi_email": e.citi_email,
            "region_code": e.region_code,
            "region_name": e.region_name,
            "default_project_code": e.default_project_code,
            "billing_rate": e.billing_rate,
            "role": e.role,
            "manager": e.manager,
            "status": e.status,
            "start_date": e.start_date.isoformat() if e.start_date else None,
            "end_date": e.end_date.isoformat() if e.end_date else None,
        }
        for e in rows
    ]


@app.post("/api/employees")
def create_employee(payload: dict = Body(...), db: Session = Depends(get_db)):
    citi_email = (payload.get("citi_email") or "").strip().lower() or None
    employee_id = (payload.get("employee_id") or "").strip() or None

    # Try to find existing record using citi_email or employee_id
    existing = None
    if citi_email or employee_id:
        conditions = []
        if citi_email:
            conditions.append(Employee.citi_email == citi_email)
        if employee_id:
            conditions.append(Employee.employee_id == employee_id)
        existing = db.execute(
            select(Employee).where(or_(*conditions))
        ).scalar_one_or_none()

    start_date = (
        datetime.date.fromisoformat(payload["start_date"])
        if payload.get("start_date")
        else datetime.date.today()
    )
    end_date = (
        datetime.date.fromisoformat(payload["end_date"])
        if payload.get("end_date")
        else None
    )

    if existing:
        # 🔄 Update existing employee instead of creating a duplicate
        e = existing
        e.employee_id = employee_id or e.employee_id
        e.name = payload.get("name") or e.name
        e.cg_email = payload.get("cg_email") or e.cg_email
        e.citi_email = citi_email or e.citi_email
        e.region_code = payload.get("region_code") or e.region_code
        e.region_name = payload.get("region_name") or e.region_name
        e.default_project_code = (
            payload.get("default_project_code") or e.default_project_code
        )
        if payload.get("billing_rate") is not None:
            e.billing_rate = float(payload.get("billing_rate") or 0.0)
        e.role = payload.get("role") or e.role
        e.manager = payload.get("manager") or e.manager
        e.status = payload.get("status") or e.status or "Active"
        e.start_date = e.start_date or start_date
        e.end_date = end_date or e.end_date
    else:
        # 🆕 Brand new employee
        e = Employee(
            employee_id=employee_id,
            name=payload.get("name"),
            cg_email=payload.get("cg_email"),
            citi_email=citi_email,
            region_code=payload.get("region_code"),
            region_name=payload.get("region_name"),
            default_project_code=payload.get("default_project_code"),
            billing_rate=float(payload.get("billing_rate") or 0.0),
            role=payload.get("role"),
            manager=payload.get("manager"),
            status=payload.get("status") or "Active",
            start_date=start_date,
            end_date=end_date,
        )
        db.add(e)

    db.commit()
    db.refresh(e)

    # 🔗 Link onboarding/update to timesheet: ensure skeleton row for current month
    if e.citi_email:
        today = datetime.date.today()
        ym = f"{today.year:04d}-{today.month:02d}"
        exists_recon = db.execute(
            select(ReconEntry).where(
                ReconEntry.month == ym, ReconEntry.citi_email == e.citi_email
            )
        ).scalar_one_or_none()

        if not exists_recon:
            expected = expected_hours_for_month(today.year, today.month, None)
            db.add(
                ReconEntry(
                    employee_id=e.employee_id,
                    month=ym,
                    name=e.name,
                    cg_email=e.cg_email,
                    citi_email=e.citi_email,
                    region_code=e.region_code,
                    region_name=e.region_name,
                    project_name=None,
                    project_code=e.default_project_code or "UNKNOWN",
                    billing_rate=e.billing_rate or 0.0,
                    total_hours_cg=expected,
                    submitted_hours_cg=0.0,
                    submitted_on_cg=None,
                    status_cg="Not Completed",
                    total_hours_citi=expected,
                    submitted_hours_citi=0.0,
                    holidays=None,
                    status_citi="Not Completed",
                    expected_hours=expected,
                    reconciled_hours=0.0,
                    reconciled_status="Not Completed",
                    reminders=0,
                )
            )
            db.commit()

    return {"id": e.id}


@app.put("/api/employees/{emp_id}")
def update_employee(emp_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(404, "Employee not found")

    for field in [
        "employee_id",
        "name",
        "cg_email",
        "citi_email",
        "region_code",
        "region_name",
        "default_project_code",
        "role",
        "manager",
        "status",
    ]:
        if field in payload:
            setattr(e, field, payload[field])

    if "billing_rate" in payload:
        e.billing_rate = float(payload["billing_rate"] or 0.0)

    if "start_date" in payload:
        e.start_date = (
            datetime.date.fromisoformat(payload["start_date"])
            if payload["start_date"]
            else None
        )
    if "end_date" in payload:
        e.end_date = (
            datetime.date.fromisoformat(payload["end_date"])
            if payload["end_date"]
            else None
        )

    db.commit()
    return {"ok": True}


@app.post("/api/employees/{emp_id}/onboard")
def onboard_employee(emp_id: int, db: Session = Depends(get_db)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    e.status = "Active"
    if not e.start_date:
        e.start_date = datetime.date.today()
    e.end_date = None
    db.commit()
    return {"ok": True}


@app.post("/api/employees/{emp_id}/deboard")
def deboard_employee(emp_id: int, db: Session = Depends(get_db)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    e.status = "Inactive"
    e.end_date = datetime.date.today()
    db.commit()
    return {"ok": True}


@app.post("/api/employees/deduplicate")
def deduplicate_employees(db: Session = Depends(get_db)):
    """
    One-time helper: keeps the first employee per (citi_email or employee_id)
    and deletes extra duplicates.
    Call this once via Postman/curl if you already have duplicates.
    """
    rows = db.execute(select(Employee)).scalars().all()
    seen_keys = set()
    to_delete = []

    for e in rows:
        key = (e.citi_email or "").lower() or (e.employee_id or "")
        if not key:
            # treat empty key as unique
            key = f"__tmp_{e.id}"
        if key in seen_keys:
            to_delete.append(e.id)
        else:
            seen_keys.add(key)

    if to_delete:
        db.execute(Employee.__table__.delete().where(Employee.id.in_(to_delete)))
        db.commit()

    return {"removed": len(to_delete)}
