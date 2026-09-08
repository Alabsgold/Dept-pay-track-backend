# Backend — Departmental Payment/Contribution System
Team Visionary Coders — NACOS National Build Challenge
Backend owner: Emmanuel

## What this is
Backend for a system that lets departments create contributions (dues, event
fees, shirts, excursions) and students pay through Paystack, with automatic
verification — replacing manual "send screenshot as proof" tracking.

## Architecture
```
Frontend ↔ Backend (this repo) ↔ Database
                ↕
          Payment Gateway (Paystack)
                ↕
        AI/Analytics reads from Database
```

## Tech stack
- Python 3.11+, Django + Django REST Framework
- SQLite (local dev) / PostgreSQL (deployed)
- Auth: DRF Token Authentication
- Payment gateway: Paystack (sandbox/test mode)

## Setup (for any teammate to run this locally)
```bash
git clone <repo-url>
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in your own test keys
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Admin panel available at `http://localhost:8000/admin/`.

## Full endpoint list
See `API_CONTRACT.md` for exact request/response shapes. Summary:

| Area | Endpoints |
|---|---|
| Auth | register, login, logout, me |
| Departments | list |
| Contributions | list, create, detail, summary |
| Payments | initiate, webhook, verify, history, receipt |
| Notifications | list, mark read |
| Analytics | collection-stats, outstanding-students |

## How other roles integrate with this backend
- **Frontend:** consume the endpoints above with `Authorization: Token <token>`
  header (except register/login/webhook). See `API_CONTRACT.md` for shapes.
- **Payments teammate:** the checkout redirect flow is `POST
  /payments/initiate/` → redirect student to the returned `checkout_url`.
  You don't need to touch backend code — just the Paystack dashboard/test
  keys and the frontend checkout UX.
- **Data/AI teammate:** `GET /analytics/collection-stats/` and
  `/analytics/outstanding-students/` give you everything needed for stats or
  an AI assistant — query these, don't query the database directly.

## Progress status
_(my report to the team check backend_progress)_

| Component | Status |
|---|---|
| Users & Auth | Completed (see `docs/BACKEND_PROGRESS.md`) |
| Contributions | Up next |
| Payments (initiate + webhook) | Up next |
| Notifications | Up next |
| Tests | 10/10 passing |
| Deployed to Render | Not started |

## Known constraints
- No paid APIs — Paystack used in sandbox/test mode only
- Webhooks tested locally via ngrok, switched to real URL before demo
- See `SECURITY_AUDIT.md & BACKEND_PROGRESS.md` for issues encountered and how they were resolved
