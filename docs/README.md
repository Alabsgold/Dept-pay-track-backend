# Backend — Departmental Payment/Contribution System
Team Visionary Coders — NACOS National Build Challenge
Backend: Emmanuel

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
| Auth | register, login, logout, me, **roster import (CSV), account claim, claim batches, reset-code / reset-password, set-role** |
| Departments | list |
| Contributions | list, create, detail, summary, **roster (per-fee payments), manual mark-paid** |
| Payments | initiate, webhook, verify, history, receipt, **unverified (admin refund-review queue)** |
| Notifications | list, mark read |
| Analytics | collection-stats, outstanding-students **(deliberately NOT built — Data/AI teammate's deliverable, see `ANALYTICS_INTEGRATION.md`)** |
| Ops | **health** (`GET /api/health/` — platform probe, no auth) |

## How other roles integrate with this backend
- **Frontend:** consume the endpoints above with `Authorization: Token <token>`
  header (except register/login/claim/reset-password/departments/webhook). See
  [`API_CONTRACT.md`](./API_CONTRACT.md) for shapes and
  [`FRONTEND_LINKING.md`](./FRONTEND_LINKING.md) for the screen-by-screen wiring
  map, the error-code table and the integration checklist.
- **Payments teammate:** the checkout redirect flow is `POST
  /payments/initiate/` → redirect student to the returned `checkout_url`.
  You don't need to touch backend code — just the Paystack dashboard/test
  keys and the frontend checkout UX.
- **Data/AI teammate:** the §6 analytics endpoints are yours to build in your
  own app — `docs/ANALYTICS_INTEGRATION.md` documents the sanctioned
  read-model helpers, permission rules and response shapes. Don't query the
  database directly.

## Progress status
Current as of the Sept 18 deployment-hardening sprint (full tour in the root
[`README.md`](../README.md)):

| Component | Status |
|---|---|
| Users & Auth (+ import/claim/reset/set-role) | Complete |
| Contributions (+ payments bridge, audit rules) | Complete |
| Payments (+ settlement rules, refund queue, race-proofing, webhook proof archive) | Complete |
| Notifications | Complete |
| Tests | 200/200 passing |
| Deployment | `render.yaml` + PostgreSQL + health check ready — needs a real Render deploy + Paystack live webhook URL |

## Known constraints
- No paid APIs — Paystack used in sandbox/test mode only
- Webhooks tested locally without ngrok (`backend/simulate_webhook.py`); on
  deploy point the Paystack dashboard at the real `/api/payments/webhook/`
- Free-tier Render sleeps after ~15 min idle — ping `/api/health/` before a
  live demo so the cold start doesn't eat the slot
- See `SECURITY_AUDIT.md & BACKEND_PROGRESS.md` for issues encountered and how they were resolved
