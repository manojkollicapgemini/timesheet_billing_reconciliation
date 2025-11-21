import random
import calendar
from datetime import date, datetime
from pathlib import Path

import pandas as pd


# ---------- Config ----------

NUM_EMPLOYEES = 10
MONTHS_BACK = 24  # last 2 years
OUTPUT_PATH = Path("sample_workbook.xlsx")

# Define some projects
PROJECTS = [
    ("P100", "Payments Core", "EU", "Europe"),
    ("P200", "Risk Engine", "NA", "North America"),
    ("P300", "KYC Portal", "APAC", "APAC"),
    ("P400", "Liquidity Platform", "NA", "North America"),
    ("P500", "Trade Surveillance", "EU", "Europe"),
]

random.seed(42)


def month_list_last_n(n: int):
    """Return last n months incl current as 'YYYY-MM' strings."""
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


def random_employees(num):
    employees = []
    for i in range(num):
        eid = f"{100 + i}"
        first = random.choice(
            ["Alex", "Priya", "Maya", "Sam", "Diego", "Lea", "Ravi", "Sara", "John", "Aisha"]
        )
        last = random.choice(["Rivera", "Nair", "Chen", "Wu", "Lopez", "Singh", "Patel", "Khan"])
        name = f"{first} {last}"
        # Ensure email uniqueness
        handle = f"{first.lower()}.{last.lower()}{i}"
        cg_email = f"{handle}@capgemini.com"
        citi_email = f"{handle}@citi.com"
        rate = random.choice([75, 80, 85, 90, 95, 100])
        employees.append((eid, name, cg_email, citi_email, rate))
    return employees


def expected_hours(year: int, month: int, holidays=None):
    """8 hours per business day, excluding holidays."""
    holidays = set(holidays or [])
    _, last = calendar.monthrange(year, month)
    total = 0
    for d in range(1, last + 1):
        dt = date(year, month, d)
        if dt.weekday() >= 5:  # weekend
            continue
        if dt.isoformat() in holidays:
            continue
        total += 8
    return total


def generate_sample_workbook(out_path: Path):
    employees = random_employees(NUM_EMPLOYEES)
    months = month_list_last_n(MONTHS_BACK)

    cg_rows = []
    citi_rows = []
    cg_daily_rows = []
    citi_daily_rows = []

    # simple pseudo-holiday: 5th of Jan, Apr, Aug, Dec as example
    def month_holidays(y, m):
        hols = []
        if m in (1, 4, 8, 12):
            hols.append(date(y, m, 5).isoformat())
        return hols

    for ym in months:
        y, m = map(int, ym.split("-"))
        hols = month_holidays(y, m)
        exp_hours = expected_hours(y, m, hols)
        _, last_day = calendar.monthrange(y, m)

        # Business days
        business_days = [
            date(y, m, d)
            for d in range(1, last_day + 1)
            if date(y, m, d).weekday() < 5 and date(y, m, d).isoformat() not in hols
        ]

        for (eid, name, cg_email, citi_email, rate) in employees:
            # Assign a project for this employee-month
            pcode, pname, rcode, rname = random.choice(PROJECTS)

            # Generate daily hours
            daily_cg = {}
            daily_citi = {}
            for dt in business_days:
                # CG hours: 0, 4 or 8 with bias towards 8
                r = random.random()
                if r < 0.15:
                    h_cg = 0
                elif r < 0.3:
                    h_cg = 4
                else:
                    h_cg = 8
                daily_cg[dt] = h_cg

                # CITI hours mostly equal, some small mismatches
                if h_cg == 0:
                    h_ci = 0
                else:
                    if random.random() < 0.85:
                        h_ci = h_cg
                    else:
                        # mismatch: +/- 2 or 4, but not negative
                        delta = random.choice([-2, 2, -4, 4])
                        h_ci = max(h_cg + delta, 0)
                daily_citi[dt] = h_ci

            submitted_cg = sum(daily_cg.values())
            submitted_citi = sum(daily_citi.values())

            # Monthly CG row
            cg_rows.append(
                {
                    "ID": eid,
                    "Name": name,
                    "CG Email": cg_email,
                    "Citi Email": citi_email,
                    "Total Hours(CG)": exp_hours,
                    "Submitted Hours(CG)": submitted_cg,
                    "Submitted On": f"{ym}-18",  # synthetic
                    "Billing Rate": rate,
                    "Region Code": rcode,
                    "Region Name": rname,
                    "Project Name": pname,
                    "Project Code": pcode,
                    "Month": ym,
                }
            )

            # Monthly CITI row
            citi_rows.append(
                {
                    "Citi Email": citi_email,
                    "Total Hours(Citi)": exp_hours,
                    "Submitted Hours(Citi)": submitted_citi,
                    "Holidays": ",".join(hols),
                    "Project Code": pcode,
                    "Month": ym,
                }
            )

            # Daily CG / CITI
            for dt in business_days:
                h_cg = daily_cg[dt]
                h_ci = daily_citi[dt]

                if h_cg > 0:
                    cg_daily_rows.append(
                        {
                            "Citi Email": citi_email,
                            "Date": dt,
                            "Hours": h_cg,
                            "Project Code": pcode,
                        }
                    )
                if h_ci > 0:
                    citi_daily_rows.append(
                        {
                            "Citi Email": citi_email,
                            "Date": dt,
                            "Hours": h_ci,
                            "Project Code": pcode,
                        }
                    )

    # Build dataframes
    cg_df = pd.DataFrame(cg_rows)
    citi_df = pd.DataFrame(citi_rows)
    cg_daily_df = pd.DataFrame(cg_daily_rows)
    citi_daily_df = pd.DataFrame(citi_daily_rows)

    # Write to Excel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        cg_df.to_excel(writer, sheet_name="CG", index=False)
        citi_df.to_excel(writer, sheet_name="CITI", index=False)
        cg_daily_df.to_excel(writer, sheet_name="CG_DAILY", index=False)
        citi_daily_df.to_excel(writer, sheet_name="CITI_DAILY", index=False)

    print(f"Sample workbook generated at: {out_path.resolve()}")


if __name__ == "__main__":
    generate_sample_workbook(OUTPUT_PATH)
