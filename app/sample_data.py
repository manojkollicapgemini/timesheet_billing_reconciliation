import random
import calendar
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Fixed portfolio: one of these will be assigned to each employee
PROJECTS = [
    ("P100", "Payments Core", "EU", "Europe"),
    ("P200", "Risk Engine", "NA", "North America"),
    ("P300", "KYC Portal", "APAC", "APAC"),
    ("P400", "Liquidity Platform", "NA", "North America"),
    ("P500", "Trade Surveillance", "EU", "Europe"),
]

HOURS_PER_DAY = 8


def month_list_last_n(n: int):
    """
    Return a sorted list of 'YYYY-MM' strings for the last n months (including current).
    """
    today = date.today()
    y, m = today.year, today.month
    months = []
    for _ in range(n):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return sorted(months)


def random_employees(num: int):
    """
    Generate a list of employees with ONE fixed project each.
    Returns a list of dicts:
    {
        'employee_id', 'name', 'cg_email', 'citi_email',
        'billing_rate', 'project_code', 'project_name',
        'region_code', 'region_name'
    }
    """
    firsts = [
        "Alex",
        "Priya",
        "Maya",
        "Sam",
        "Diego",
        "Lea",
        "Ravi",
        "Sara",
        "John",
        "Aisha",
    ]
    lasts = ["Rivera", "Nair", "Chen", "Wu", "Lopez", "Singh", "Patel", "Khan", "Brown", "Smith"]

    random.seed(42)

    # Assign projects in a stable, repeated pattern
    project_cycle = []
    for i in range(num):
        pcode, pname, rcode, rname = PROJECTS[i % len(PROJECTS)]
        project_cycle.append((pcode, pname, rcode, rname))

    employees = []
    for i in range(num):
        f = random.choice(firsts)
        l = random.choice(lasts)
        name = f"{f} {l}"
        handle = f"{f.lower()}.{l.lower()}{i}"
        cg_email = f"{handle}@capgemini.com"
        citi_email = f"{handle}@citi.com"
        rate = random.choice([75, 80, 85, 90, 95, 100])
        pcode, pname, rcode, rname = project_cycle[i]

        employees.append(
            {
                "employee_id": str(100 + i),
                "name": name,
                "cg_email": cg_email,
                "citi_email": citi_email,
                "billing_rate": rate,
                "project_code": pcode,
                "project_name": pname,
                "region_code": rcode,
                "region_name": rname,
            }
        )
    return employees


def month_holidays(year: int, month: int):
    """
    Simplified holiday model: some fixed days to make the calendar realistic.
    """
    hols = []
    # Put a single "local holiday" on day 5 for some months
    if month in (1, 4, 8, 12):
        hols.append(date(year, month, 5).isoformat())
    return hols


def expected_hours(year: int, month: int, holidays):
    """
    Compute expected hours for the month (Mon–Fri, excluding holidays, 8h/day).
    """
    holidays = set(holidays or [])
    _, last = calendar.monthrange(year, month)
    total = 0
    for d in range(1, last + 1):
        dt = date(year, month, d)
        if dt.weekday() >= 5 or dt.isoformat() in holidays:
            continue
        total += HOURS_PER_DAY
    return total


def generate_timeoff_for_month(business_days, probability=0.3):
    """
    Optionally generate a contiguous block of 1–3 days time-off in the given month.
    Returns (start_date, end_date) or (None, None).
    """
    if not business_days or random.random() >= probability:
        return None, None

    # pick a random starting business day and 1–3 working days
    start_idx = random.randrange(len(business_days))
    length = random.randint(1, 3)
    end_idx = min(start_idx + length - 1, len(business_days) - 1)

    start = business_days[start_idx]
    end = business_days[end_idx]
    return start, end


def generate_sample_workbook(path: Path, num_employees: int = 10, months_back: int = 24):
    """
    Generate an Excel workbook with CG/CITI monthly + daily timesheets and
    a TIMEOFF sheet, with:
      - one project per employee across all months,
      - a mix of Completed / Partial / Mismatch / Not Completed scenarios,
      - some sample time-off records per employee.
    """

    employees = random_employees(num_employees)
    months = month_list_last_n(months_back)

    cg_rows = []
    citi_rows = []
    cg_daily = []
    citi_daily = []
    timeoff_records = []

    # We want to guarantee we see all scenarios at least once.
    # We'll force the first employee's first 4 months, then randomise.
    forced_scenarios = ["COMPLETED", "MISMATCH", "PARTIAL", "NOT_COMPLETED"]

    random.seed(123)

    for month_index, ym in enumerate(months):
        year, month = map(int, ym.split("-"))
        hols = month_holidays(year, month)
        exp = expected_hours(year, month, hols)

        # All working days for this month
        _, last = calendar.monthrange(year, month)
        business_days = [
            date(year, month, d)
            for d in range(1, last + 1)
            if date(year, month, d).weekday() < 5 and date(year, month, d).isoformat() not in hols
        ]

        for emp_index, emp in enumerate(employees):
            eid = emp["employee_id"]
            name = emp["name"]
            cg_email = emp["cg_email"]
            citi_email = emp["citi_email"]
            rate = emp["billing_rate"]
            pcode = emp["project_code"]
            pname = emp["project_name"]
            rcode = emp["region_code"]
            rname = emp["region_name"]

            # Decide scenario for this employee-month
            if emp_index == 0 and month_index < len(forced_scenarios):
                scenario = forced_scenarios[month_index]
            else:
                scenario = random.choices(
                    ["COMPLETED", "PARTIAL", "MISMATCH", "NOT_COMPLETED"],
                    weights=[0.5, 0.25, 0.15, 0.1],
                    k=1,
                )[0]

            # Decide time-off block (optional)
            to_start, to_end = generate_timeoff_for_month(
                business_days,
                probability=0.35 if scenario != "NOT_COMPLETED" else 0.15,
            )
            if to_start and to_end:
                timeoff_records.append(
                    {
                        "Citi Email": citi_email,
                        "Start Date": to_start,
                        "End Date": to_end,
                        "Leave Type": "Planned",
                        "Reason": f"Sample PTO for {name}",
                    }
                )

            # Build daily hours based on scenario
            daily_cg = {}
            daily_ci = {}

            def is_timeoff_day(dt: date) -> bool:
                if not to_start or not to_end:
                    return False
                return to_start <= dt <= to_end

            if scenario == "NOT_COMPLETED":
                # No hours at all this month
                for dt in business_days:
                    daily_cg[dt] = 0
                    daily_ci[dt] = 0

            elif scenario == "COMPLETED":
                # Work full days except time-off; Citi mirrors CG
                for dt in business_days:
                    if is_timeoff_day(dt):
                        # leave – no hours
                        daily_cg[dt] = 0
                        daily_ci[dt] = 0
                    else:
                        daily_cg[dt] = HOURS_PER_DAY
                        daily_ci[dt] = HOURS_PER_DAY

            elif scenario == "PARTIAL":
                # Only some working days are filled; Citi mirrors CG
                for dt in business_days:
                    if is_timeoff_day(dt):
                        daily_cg[dt] = 0
                        daily_ci[dt] = 0
                    else:
                        # 70% chance of working that day
                        if random.random() < 0.7:
                            daily_cg[dt] = HOURS_PER_DAY
                            daily_ci[dt] = HOURS_PER_DAY
                        else:
                            daily_cg[dt] = 0
                            daily_ci[dt] = 0

            elif scenario == "MISMATCH":
                # CG is mostly full; CITI mirrors but with some random differences
                for dt in business_days:
                    if is_timeoff_day(dt):
                        daily_cg[dt] = 0
                        daily_ci[dt] = 0
                    else:
                        daily_cg[dt] = HOURS_PER_DAY
                        # mismatch on ~25% of working days
                        if random.random() < 0.25:
                            # slightly off, e.g. 4h or 0h
                            daily_ci[dt] = random.choice([0, 4])
                        else:
                            daily_ci[dt] = HOURS_PER_DAY

            # Calculate monthly submitted hours from daily pattern
            submitted_cg = sum(daily_cg.values())
            submitted_ci = sum(daily_ci.values())

            # CG monthly row
            cg_rows.append(
                {
                    "ID": eid,
                    "Name": name,
                    "CG Email": cg_email,
                    "Citi Email": citi_email,
                    "Total Hours(CG)": exp,  # theoretical expected
                    "Submitted Hours(CG)": submitted_cg,
                    "Submitted On": f"{ym}-18",
                    "Billing Rate": rate,
                    "Region Code": rcode,
                    "Region Name": rname,
                    "Project Name": pname,
                    "Project Code": pcode,
                    "Month": ym,
                }
            )

            # CITI monthly row
            citi_rows.append(
                {
                    "Citi Email": citi_email,
                    "Total Hours(Citi)": exp,
                    "Submitted Hours(Citi)": submitted_ci,
                    "Holidays": ",".join(hols),
                    "Project Code": pcode,
                    "Month": ym,
                }
            )

            # Daily tables
            for dt in business_days:
                h_cg = daily_cg[dt]
                h_ci = daily_ci[dt]
                if h_cg > 0:
                    cg_daily.append(
                        {
                            "Citi Email": citi_email,
                            "Date": dt,
                            "Hours": h_cg,
                            "Project Code": pcode,
                        }
                    )
                if h_ci > 0:
                    citi_daily.append(
                        {
                            "Citi Email": citi_email,
                            "Date": dt,
                            "Hours": h_ci,
                            "Project Code": pcode,
                        }
                    )

    # Build DataFrames
    cg_df = pd.DataFrame(cg_rows)
    citi_df = pd.DataFrame(citi_rows)
    cg_daily_df = pd.DataFrame(cg_daily)
    citi_daily_df = pd.DataFrame(citi_daily)
    timeoff_df = pd.DataFrame(timeoff_records)

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        cg_df.to_excel(writer, sheet_name="CG", index=False)
        citi_df.to_excel(writer, sheet_name="CITI", index=False)
        cg_daily_df.to_excel(writer, sheet_name="CG_DAILY", index=False)
        citi_daily_df.to_excel(writer, sheet_name="CITI_DAILY", index=False)
        if not timeoff_df.empty:
            # Optional sample time-off sheet the app can later import
            timeoff_df.to_excel(writer, sheet_name="TIMEOFF", index=False)


def ensure_sample_workbook(path: Path):
    """
    Generate the workbook only if it doesn't already exist.
    """
    if not path.exists():
        generate_sample_workbook(path)
    else:
        print(f"Sample workbook already exists at {path}, skipping generation.")
