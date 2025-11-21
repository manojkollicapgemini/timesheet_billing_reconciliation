# CG × Citi — Timesheet, Billing & Chatbot

FastAPI + Bootstrap app for:

- CG vs CITI timesheet reconciliation (monthly + daily)
- Portfolio-style billing analytics (per project, per resource)
- LLM chatbot using Capgemini Generative Engine (Mistral)

## Features

- Upload workbook with sheets: `CG`, `CITI`, `CG_DAILY`, `CITI_DAILY`
- Or click **Use Sample** to load generated data:
  - 10 employees
  - Last 24 months (2 years)
  - Multiple projects (P100..P500)

- Timesheets page:
  - Status per resource (CG / CITI / reconciled)
  - Daily drilldown (CG vs CITI per day)
  - Reminder count & "Send Reminder" action

- Billing page (login required):
  - Login: `admin` / `password`
  - Project filter
  - Resource-wise billing table
  - Billing by project (bar chart)
  - Monthly trend (line chart)
  - Annual projection using linear regression (not just ×12)

- Chatbot page:
  - Uses Capgemini Generative Engine API
  - Summarises billing & status context from live DB
  - Answers portfolio-style questions

## Setup

```bash
cd cg_citi_portfolio
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt
```

Create `.env` in the project root:

```env
CAPG_LLM_API_KEY=YOUR_CAPGEMINI_API_KEY_HERE
```

## Run

```bash
uvicorn app.main:app --reload
```

Then open:

- Timesheets: http://127.0.0.1:8000/
- Billing (login): http://127.0.0.1:8000/billing
- Chatbot: http://127.0.0.1:8000/chatbot
- Admin (grid upload): http://127.0.0.1:8000/admin
