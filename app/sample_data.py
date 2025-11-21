import random
import calendar
from datetime import date
from pathlib import Path

import pandas as pd

PROJECTS = [
    ("P100", "Payments Core", "EU", "Europe"),
    ("P200", "Risk Engine", "NA", "North America"),
    ("P300", "KYC Portal", "APAC", "APAC"),
    ("P400", "Liquidity Platform", "NA", "North America"),
    ("P500", "Trade Surveillance", "EU", "Europe"),
]


def month_list_last_n(n: int):
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
    emps = []
    firsts = ["Alex", "Priya", "Maya", "Sam", "Diego", "Lea", "Ravi", "Sara", "John", "Aisha"]
    lasts = ["Rivera", "Nair", "Chen", "Wu", "Lopez", "Singh", "Patel", "Khan", "Brown", "Smith"]
    random.seed(42)
    for i in range(num):
        f = random.choice(firsts)
        l = random.choice(lasts)
        name = f"{f} {l}"
        handle = f"{f.lower()}.{l.lower()}{i}"
        cg_email = f"{handle}@capgemini.com"
        citi_email = f"{handle}@citi.com"
        rate = random.choice([75, 80, 85, 90, 95, 100])
        emps.append((str(100 + i), name, cg_email, citi_email, rate))
    return emps


def month_holidays(year: int, month: int):
    hols = []
    if month in (1, 4, 8, 12):
        hols.append(date(year, month, 5).isoformat())
    return hols


def expected_hours(year: int, month: int, holidays: list[str]):
    _, last = calendar.monthrange(year, month)
    total = 0
    for d in range(1, last + 1):
        dt = date(year, month, d)
        if dt.weekday() >= 5 or dt.isoformat() in holidays:
            continue
        total += 8
    return total


def generate_sample_workbook(path: Path, num_employees: int = 10, months_back: int = 24):
    emps = random_employees(num_employees)
    months = month_list_last_n(months_back)

    cg_rows = []
    citi_rows = []
    cg_daily = []
    citi_daily = []

    random.seed(123)

    for ym in months:
        year, month = map(int, ym.split("-"))
        hols = month_holidays(year, month)
        exp = expected_hours(year, month, hols)

        _, last = calendar.monthrange(year, month)
        business_days = [
            date(year, month, d)
            for d in range(1, last + 1)
            if date(year, month, d).weekday() < 5 and date(year, month, d).isoformat() not in hols
        ]

        for eid, name, cg_email, citi_email, rate in emps:
            pcode, pname, rcode, rname = random.choice(PROJECTS)

            daily_cg = {}
            daily_ci = {}

            for dt in business_days:
                r = random.random()
                if r < 0.15:
                    h_cg = 0
                elif r < 0.3:
                    h_cg = 4
                else:
                    h_cg = 8
                daily_cg[dt] = h_cg

                if h_cg == 0:
                    h_ci = 0
                else:
                    if random.random() < 0.85:
                        h_ci = h_cg
                    else:
                        delta = random.choice([-2, 2, -4, 4])
                        h_ci = max(h_cg + delta, 0)
                daily_ci[dt] = h_ci

            submitted_cg = sum(daily_cg.values())
            submitted_ci = sum(daily_ci.values())

            cg_rows.append(
                {
                    "ID": eid,
                    "Name": name,
                    "CG Email": cg_email,
                    "Citi Email": citi_email,
                    "Total Hours(CG)": exp,
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

    cg_df = pd.DataFrame(cg_rows)
    citi_df = pd.DataFrame(citi_rows)
    cg_daily_df = pd.DataFrame(cg_daily)
    citi_daily_df = pd.DataFrame(citi_daily)

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        cg_df.to_excel(writer, sheet_name="CG", index=False)
        citi_df.to_excel(writer, sheet_name="CITI", index=False)
        cg_daily_df.to_excel(writer, sheet_name="CG_DAILY", index=False)
        citi_daily_df.to_excel(writer, sheet_name="CITI_DAILY", index=False)


def ensure_sample_workbook(path: Path):
    if not path.exists():
        generate_sample_workbook(path)
