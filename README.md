# Departmental Payment/Contribution System — Backend
Team Visionary Coders — NACOS National Build Challenge

Django + DRF backend that lets departments create contributions (dues, event
fees, shirts, excursions) and students pay through Paystack with automatic
verification — replacing manual "send screenshot as proof" tracking.

## Tech stack
- Python 3.11+, Django 5.2 + Django REST Framework
- SQLite (local dev) / PostgreSQL (deployed)
- Auth: DRF token authentication
- Payment gateway: Paystack (sandbox/test mode)

## Setup (any teammate can run this)
```bash
git clone <repo-url>
cd Departmental-Payment-Tracking/backend
python -m venv venv
venv\Scripts\activate                # Windows   (mac/linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env               # then fill in your own Paystack test keys
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Admin panel: `http://localhost:8000/admin/`

## API
Endpoints, request/response shapes, error codes, and the binding security
rules live in [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md).
Base URL (local dev): `http://localhost:8000/api/`

Quick summary:

| Area | Endpoints |
|---|---|
| Auth | register, login, logout, me |
| Departments | list |
| Contributions | list, create, detail, summary, payments list |
| Payments | initiate, webhook, verify, history, receipt |
| Notifications | list, mark read |
| Analytics | collection-stats, outstanding-students |

## Integrating roles
- **Frontend:** send `Authorization: Token <token>` on every request except
  register/login/webhook. Handle `429` with the standard error shape.
- **Payments teammate:** `POST /payments/initiate/` → redirect the student to
  the returned `checkout_url`. No backend code changes needed on your side.
- **Data/AI teammate:** consume the `/analytics/` endpoints via a dedicated
  class-rep-role service account — never query the database directly.

## Project layout
- `backend/core/` — settings, URL config, exception handler
- `backend/apps/` — one Django app per concern (users, contributions, payments, notifications)
- `docs/` — API contract, DB structure, security audit report
