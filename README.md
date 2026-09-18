# Departmental Payment/Contribution System — Backend

**Team Visionary Coders** — NACOS National Build Challenge
**Branch:** `backend-dev` · **Status:** all 5 build modules complete · **156/156 tests passing**

Django + DRF backend that lets departments create contributions (dues, event
fees, shirts, excursions) and students pay through Paystack with automatic
verification — replacing manual "send screenshot as proof" tracking.

It also solves the tracking problem *before* anyone pays: admins import the
department's class list, so the system knows exactly **who owes what**, who has
paid, and who has not — instead of only counting students who happened to
register.

---

## Contents
1. [Tech stack](#tech-stack)
2. [Setup (any teammate can run this)](#setup-any-teammate-can-run-this)
3. [How the system works — feature tour](#how-the-system-works--feature-tour)
4. [Architecture & how it was built](#architecture--how-it-was-built)
5. [Full API reference](#full-api-reference)
6. [The payment lifecycle (most important section)](#the-payment-lifecycle-most-important-section)
7. [Auth, roles & security rules](#auth-roles--security-rules)
8. [Roster import & account claiming](#roster-import--account-claiming)
9. [Password reset (assisted, no email)](#password-reset-assisted-no-email)
10. [Rep promotion](#rep-promotion)
11. [Notifications](#notifications)
12. [Analytics handover (Data/AI teammate)](#analytics-handover-dataai-teammate)
13. [Testing](#testing)
14. [Deployment](#deployment)
15. [Known open items / roadmap](#known-open-items--roadmap)
16. [Notes for teammates](#notes-for-teammates)

---

## Tech stack
- Python 3.11+, Django 5.2 + Django REST Framework
- SQLite (local dev) / PostgreSQL (deployed)
- Auth: DRF token authentication
- Payment gateway: Paystack (sandbox/test mode)
- **No paid packages, no Celery, no third-party notification or CSV libraries.**
  Everything is built on Django + DRF only.

---

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

**Required `.env` values** (see `.env.example`):

| Key | Notes |
|---|---|
| `SECRET_KEY` | Required — the app refuses to boot without it |
| `DEBUG` | Defaults to `False`; set `True` locally only |
| `PAYSTACK_SECRET_KEY` | Required — the webhook HMAC key; no insecure default |
| `PAYSTACK_PUBLIC_KEY` | Optional (frontend only; backend never uses it) |
| `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` | Needed once deployed |

## How the system works — feature tour

**Three roles** (enforced in `backend/apps/users/permissions.py`):

| Role | Can do |
|---|---|
| `student` | See department fees, pay online, view own history/receipts, read own notifications, edit own profile, claim an imported account |
| `class_rep` | Everything a student can, **plus** create fees, view rosters, mark students paid for **offline** payments, issue reset codes |
| `admin` | Everything, plus mark-paid for anyone, promote users to rep, import rosters, review refunds. **Exempt from paying dues.** |

> **Note:** a class rep **is a student and still owes dues.** Only admins/staff
> are exempt. This is enforced in `eligible_students()` and therefore flows into
> `total_expected` and `outstanding_count`.

**Student journey**
1. Admin imports the class list → the student's account already exists (inert).
2. Student claims it (matric + first name + batch code) and sets a password.
3. Student logs in, sees fees for their level, taps **Pay**.
4. Backend creates a Payment and returns a Paystack `checkout_url`; the student
   pays. Paystack calls our webhook; **Verify** is the manual safety net.
5. Student sees a receipt, history and a notification; the roster shows them paid.

**Class rep journey**
- Creates fees (title, amount, optional target level, deadline).
- Views the roster: who paid, who has not, total collected, and which rep
  recorded each offline payment (`recorded_by` + `receipt_reference`).
- Marks offline payments — **must** quote a teller/receipt number, and the
  student is notified automatically so mistakes get caught.

**Admin journey**
- Imports/refreshes rosters, promotes reps, reviews refund flags, exempt from dues.

## Architecture & how it was built

```
backend/
├── core/                    # settings, root URLs, custom exception handler
├── apps/
│   ├── users/               # auth, roles, departments, import/claim, reset, set-role
│   ├── contributions/       # fees, roster, summary, mark-paid + payments_bridge
│   ├── payments/            # Paystack initiate/webhook/verify/history/receipt + settlement
│   └── notifications/       # in-app alerts + signal triggers
└── simulate_webhook.py      # local Paystack webhook signer (no ngrok needed)
```

**The build was split across 5 agent passes**, each with a definition of done in
`directives/AGENTS.md`:

| Agent | Scope | Outcome |
|---|---|---|
| 1 | Users & auth | register/login/logout/me, departments, roles, permissions, throttling |
| 2 | Contributions | CRUD, visibility scoping, summary, payments bridge |
| 3 | Payments | Paystack initiate, webhook (HMAC + idempotent), verify, history, receipt |
| 4 | Notifications | in-app model, list/mark-read, transition-only triggers |
| 5 | QA | per-endpoint contract tests across all four apps |

Then a **bug-hunt/fix pass** on the money paths, and a **launch-readiness pass**
(roster import, claiming, assisted reset, rep promotion, offline-payment audit).

### The design decisions that matter

| Decision | Why |
|---|---|
| **Money is always `Decimal`, never `float`** | `DecimalField(max_digits=10, decimal_places=2)`; zero `FloatField`/`float()` in `apps/`. Kobo conversion is integer-only: `int(Decimal * 100)` |
| **Amounts are always server-side** | The client sends only a `contribution_id`; it can never propose a price |
| **One cross-app coordination point: signals** | `notifications` listens to `Payment`/`Contribution` `post_save`. No app reaches into another's models to mutate them |
| **Alerts fire only on real status transitions** | A `pre_save` snapshot stops webhook retries and re-verifies duplicating notifications |
| **Every error uses one shape** | `{"error": "<code>", "message": "<human text>"}` via `core.exceptions` |
| **Existence is never leaked** | Foreign objects return **404, not 403**; duplicate registration returns a generic message |
| **Refunds are never automatic** | A mismatch flags `refund_status = pending_review`; a human decides (audit requirement) |


## Full API reference

Base URL (local dev): `http://localhost:8000/api/`
Full request/response shapes: [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md).

### Auth, users & departments (`/api/`)
| Method | Path | Who | Purpose |
|---|---|---|---|
| `POST` | `/auth/register/` | public | Self-register (fallback if a student is missing from the roster) |
| `POST` | `/auth/login/` | public | Returns a token (rotated on every login) |
| `POST` | `/auth/logout/` | any | Invalidates the token server-side |
| `GET`/`PATCH` | `/auth/me/` | any | Read / edit own profile (identity fields locked) |
| `GET` | `/departments/` | public | Department list for signup dropdowns |
| `POST` | `/auth/import/` | admin | Bulk roster CSV import (`dry_run` supported) |
| `POST` | `/auth/claim/` | public | Claim an imported account: matric + first name + batch code |
| `GET` | `/auth/claim-batches/` | admin | List claim batches/codes |
| `POST` | `/auth/claim-batches/{id}/deactivate/` | admin | Retire a batch code |
| `POST` | `/auth/reset-code/` | rep/admin | Issue a one-time student reset code |
| `POST` | `/auth/reset-password/` | public | Redeem the code and set a new password |
| `POST` | `/auth/users/{id}/set-role/` | admin | Promote/demote a student ↔ class rep |

### Contributions (`/api/contributions/`)
| Method | Path | Who | Purpose |
|---|---|---|---|
| `GET` | `/` | any | Fees visible to the caller (own department + level, not expired) |
| `POST` | `/` | rep/admin | Create a fee |
| `GET` | `/{id}/` | any | Fee detail |
| `GET` | `/{id}/summary/` | any | Expected / collected / outstanding counts |
| `GET` | `/{id}/payments/` | rep/admin | Roster: who paid, how, and who recorded it |
| `POST` | `/{id}/payments/` | rep/admin | Mark a student paid **offline** (requires `receipt_reference`) |

### Payments (`/api/payments/`)
| Method | Path | Who | Purpose |
|---|---|---|---|
| `GET` | `/history/` | student | Own payment history |
| `POST` | `/initiate/` | student | Create a payment, get `checkout_url` |
| `GET` | `/verify/{reference}/` | student | Re-check the gateway and settle the payment |
| `GET` | `/{id}/receipt/` | owner | Receipt for one payment |
| `POST` | `/webhook/` | Paystack | HMAC-signed gateway callback (idempotent) |
| `GET` | `/unverified/` | admin | **Refund-review queue**: payments flagged `pending_review` (wrong amount, duplicate charge, unverifiable). Read-only — approving/refusing a refund happens in the Django admin |

### Notifications (`/api/notifications/`)
| Method | Path | Who | Purpose |
|---|---|---|---|
| `GET` | `/` | any | Own notifications (badge count = `is_read == false`) |
| `POST` | `/{id}/read/` | owner | Mark one as read (idempotent) |

> `/api/analytics/` appears in the contract but is **not built** — it belongs to
> the Data/AI teammate. See `docs/ANALYTICS_INTEGRATION.md`.

---

## The payment lifecycle (most important section)

```
student taps Pay
  └─ POST /payments/initiate/        → server creates Payment(status=pending)
                                       returns { reference, checkout_url }
       └─ student pays on Paystack
            ├─ Paystack → POST /payments/webhook/   (primary, HMAC-signed)
            └─ GET /payments/verify/{ref}/          (manual safety net)
                 └─ settlement rules apply (below)
```

### Settlement rules — the money rules

| Situation | Result |
|---|---|
| Paid amount **exactly equals** the fee | `status = success` |
| Paid **less** | `status = failed`, `refund_status = pending_review` |
| Paid **more** | `status = failed`, `refund_status = pending_review` |
| Amount **unverifiable** in the payload | `status = failed`, `refund_status = pending_review` |
| Student already has a `success` for this fee (**duplicate charge**) | Failed + refund review — they were double-charged and must be refunded |
| Gateway says `abandoned`/`pending`/`ongoing`/`processing` | Row left untouched (still `pending`; the student may retry) |

⚠️ **Overpayment is deliberately *not* auto-accepted.** The logic is
that any mismatch (more *or* less) fails, flags a refund for **human review**,
and tells the student which way they were off. **Refunds are never issued
automatically** — an admin reviews and marks `refunded`/`rejected` (in the
Django admin). The admin's worklist is `GET /api/payments/unverified/`, a
read-only queue of every `pending_review` payment showing what was received
vs. expected and the difference. Automatic refunds are a legal/financial risk
we deliberately do not take.

### Webhook safety

| Guard | Implementation |
|---|---|
| Signature | HMAC-SHA512 over the **raw** body vs `x-paystack-signature`, compared with `compare_digest` |
| Lookup | **Exact `reference` string only.** Never "the latest pending payment for this student" |
| Idempotency | An already-`success` payment is not re-credited on retries |
| No re-failing | A webhook retry can never overwrite an existing refund flag |
| Response | Always `{"received": true}` |
| Proof | Every verified webhook is archived **verbatim** in `payments.Transaction` (first delivery per reference; retries not duplicated) — the evidence trail for refund disputes |
| Robustness | Non-JSON body → `400`; missing `data` → `400`; missing `amount` → flagged review (never a 500) |
| Timeout | `timeout=10` on every Paystack call, wrapped as `502 gateway_unavailable` |

### A payment can only ever be `success` if

1. the gateway reported success **and** 2. the amount matched **and**
3. this payment was not already settled. (Verify used to trust the status alone —
that was a money hole and is now fixed and regression-tested.)

---

## Auth, roles & security rules

- **Tokens:** DRF token auth; rotated on every login; invalidated on logout.
- **Throttling:** `10/min` on login/register (and other sensitive writes).
- **Passwords:** Django validators enforced (min 8, not common, not numeric-only).
- **Anti-enumeration:** duplicate registration (exact-case *or* case-insensitive,
  username/matric/email) returns **one generic message**. Field-name prefixes are
  stripped from generic errors so we never reveal *which* identifier collided.
  `first_name`, `username`, `matric_number` and `email` are declared explicitly to disable DRF's
  `UniqueValidator` oracle. DB unique constraints remain as a fail-safe.
- **Object existence:** foreign/other-department objects return **404, not 403**.
- **Propagation on deploy:** set `NUM_PROXIES=1` on Render, or the rate limiter
  sees the proxy IP and throttles *everyone* together.


## Roster import & account claiming

**Why:** without an imported roster the system only knows about students who
bothered to register — so "who hasn't paid" is meaningless and people "escape" by
never signing up. Import makes the denominator complete.

1. `POST /api/auth/import/` (admin) — CSV with `first_name, last_name,
   matric_number` (**always available**), optionally `level`, `department`,
   `email`. **Email is optional by design** — department data is often
   incomplete. Accounts are created **inert** (`set_unusable_password()`), so
   nobody can log into an unclaimed account. Use `dry_run` to preview per-row
   errors first; re-uploading the same file is **idempotent** (upsert on matric).
2. `POST /api/auth/claim/` (public) — the student supplies `matric_number` +
   `first_name` + the **batch code** and sets their own password.

**Why a batch code, not per-student passwords:** asking an admin to distribute
2,000 individual passwords is the real bottleneck. With claiming, the admin hands
out **one code per class** (printed or posted in the class group) — that is the
whole distribution task, at any scale. Batches can be listed and retired
(`claim-batches/`, `claim-batches/{id}/deactivate/`).

**Already-registered students who try to register again** get the same generic
duplicate error as everyone else — deliberately generic, so we don't hand
attackers an enumeration oracle. Their correct door is **claim** or **reset**.

**Next step for students:** complete their own profile (department, level, phone)
— we never assume the CSV carried it.

---

## Password reset (assisted, no email)

No SMTP, no email dependency, no paid service.

1. A rep/admin calls `POST /api/auth/reset-code/` for a student → returns a
   **one-time code** (short TTL, single-use).
2. The student calls `POST /api/auth/reset-password/` with `matric_number` +
   `code` + `new_password`.

Safeguards: codes expire, **cannot be replayed**, are bound to one matric number
(a code for student A cannot reset student B), and every issue is logged with
**who triggered it** — so a rep abusing resets to lock students out is visible.
This flow doubles as the recovery path for a maliciously-claimed account.

---

## Rep promotion

Reps are **ordinary students** until an admin promotes them:
`POST /api/auth/users/{id}/set-role/` (admin only).

- Cannot be used to grant `admin` — only student ↔ class rep.
- An admin cannot change their own role (prevents accidental lockout).
- This is why the frontend gets its own admin-panel action instead of relying on
  Django admin.

---

## Notifications

In-app only (the bell dropdown — no email/SMS). Created automatically by signals,
never by the client (there is deliberately **no create endpoint**):

| Trigger | Type |
|---|---|
| Payment settles as success | `payment_success` |
| Payment settles as failed / mismatch | `payment_failed` |
| A new fee is published | `new_contribution` (one per eligible student, `bulk_create`) |

- Fires **only on a real status transition** — webhook retries, re-verifies and
  idempotent re-saves stay silent.
- Manual (offline) marks go through the same path, so the student is told *"your
  rep recorded an offline payment"* — that is the audit control: students catch
  bad marks themselves.
- Marking read is **idempotent**; another user's notification returns **404**.


## Analytics handover (Data/AI teammate)

`/api/analytics/` is intentionally **not built** by the backend (Emmanuel) — it is the
Data/AI teammate's deliverable. To keep their work clean *and* protect this code:

- Read-model helpers already exist and are the sanctioned data source:
  `Contribution.total_expected()`, `.total_collected()`, `.outstanding_count()`,
  `.eligible_students()`, plus `contributions/payments_bridge.py`.
- Access should go through a dedicated **service account** with
  `IsClassRepOrAdmin`, never a direct DB connection.
- Full brief, expected §6 response shapes and mutual sandboxing rules:
  [`docs/ANALYTICS_INTEGRATION.md`](docs/ANALYTICS_INTEGRATION.md).

**Rules of engagement:** teammates consume our API and helpers; they do not edit
`apps/`. Our contract tests are the guardrail — if a change breaks one, it is
reconciled in `API_CONTRACT.md` first, never patched silently.

---

## Testing

```bash
cd backend
python manage.py test            # full suite — 156 tests
python manage.py check           # system check
python manage.py makemigrations --check --dry-run   # model drift check
python db_backup.py              # snapshot db.sqlite3 -> backups/ (downloadable)
```

**156 tests**, split by concern:

| App | Focus |
|---|---|
| `users` | auth, roles, permissions, throttling, contract shapes, import/claim/reset/set-role |
| `contributions` | CRUD, visibility scoping, summary maths, mark-paid audit rules |
| `payments` | initiate/webhook/verify/receipt, kobo maths, **every settlement rule**, idempotency, admin refund-review queue |
| `notifications` | list/mark-read ownership, transition-only triggers, no duplicates |

Test types:
- `tests.py` — behaviour and security tests.
- `tests_contract.py` — locks the documented response shapes per endpoint.
- `tests_roster.py` — import, claiming, reset and rep-promotion flows.

Tests run with `MD5PasswordHasher` only when `'test'` is in `sys.argv`, so the
suite stays fast (a few seconds) without weakening production password hashing.

### Manual / live testing (no frontend needed)

Two resources exist so you can prove the whole system works end to end:

- **[`docs/MANUAL_TESTING_GUIDE.md`](docs/MANUAL_TESTING_GUIDE.md)** — a
  step-by-step walkthrough (register 2 students + a rep → create a fee → pay with
  the Paystack test card → webhook → verify → history → notifications → roster),
  plus the negative paths (401/403/404/409/429, bad signature) and a table
  showing where to *see* each fix working.
- **`backend/simulate_webhook.py`** — signs Paystack-shaped webhooks locally, so
  **no ngrok is needed**:
  ```bash
  python simulate_webhook.py <reference>              # success
  python simulate_webhook.py <reference> 35000        # amount mismatch → failed
  ```
  Run it twice to prove idempotency (no double-credit, no duplicate notification).

---

## Deployment (Render)

The repo ships **`render.yaml`** — a one-click blueprint. In Render: *New + →
Blueprint*, pick this repo, fill the two prompted values (`PAYSTACK_SECRET_KEY`,
`CORS_ALLOWED_ORIGINS`), and it provisions the web service **plus managed
PostgreSQL**, runs `collectstatic` + `migrate` (pre-deploy), and health-checks
`GET /api/health/`.

| What the blueprint handles | Why it matters |
|---|---|
| `DATABASE_URL` → PostgreSQL | SQLite on Render is wiped on redeploy — money records must not live there. **Locally SQLite stays the default**, and `python backend/db_backup.py` snapshots `db.sqlite3` any time |
| `NUM_PROXIES=1` | The limiter reads the real client IP from `X-Forwarded-For`; without it every student shares Render's proxy IP and the `10/min` throttle locks out everyone at once |
| `SECRET_KEY` generated + `DEBUG=False` | HSTS / SSL redirect / secure cookies engage when `DEBUG=False` (already coded) |
| `ALLOWED_HOSTS=.onrender.com` | Django rejects unknown hosts |
| `preDeployCommand: migrate` | Schema applies before the new version serves traffic |
| `healthCheckPath: /api/health/` | Render restarts an unhealthy instance; also handy pre-demo |

One manual step after the first deploy: point the Paystack dashboard webhook
at `https://<your-domain>/api/payments/webhook/` — the webhook is the
**primary** settlement path.

**Free-tier demo note:** free Render services sleep after ~15 min idle and
cold-start in ~50s. Ping `/api/health/` (or run a Starter plan) before the
judges' demo so the wake-up doesn't eat your slot.

---

## Known open items / roadmap

| Item | Status |
|---|---|
| §6 `/api/analytics/` endpoints | **Deliberately not built** — Data/AI teammate's deliverable; handover in `docs/ANALYTICS_INTEGRATION.md` |
| ~~`Transaction` model (raw webhook payload proof)~~ | **Built** (Sept 18 sprint): every verified webhook's raw payload is archived once per reference in `payments.Transaction`, visible read-only in Django admin |
| Installment / partial payments | Not built. Settlement currently requires an **exact** amount, so installments need a per-fee "minimum amount" mode first |
| Notifications response field `notification_type` | Shipped as-is; frontend to confirm the name |
| Refund reconciliation report | The data already exists (`recorded_by`, `receipt_reference`, `Transaction` proofs); the report is not built |
| Automatic refunds | **Deliberately never** — every refund is human-reviewed |
| Email-based password reset | Replaced by assisted reset codes (no SMTP dependency); school email can be added post-hackathon |
| Students completing their own profile | Students *can* `PATCH /auth/me/`; a "complete your profile" prompt is not built |

---

## Notes for teammates

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

**written by** `alabiemmanuel`
