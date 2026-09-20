# Backend API Contract — Departmental Payment/Contribution System
Team Visionary Coders — NACOS National Build Challenge
**v2.1 — frontend integration hardening, verified by the Phase 1 test suite**

This is what the backend exposes. Frontend builds against these endpoints;
whoever's on the Payment Gateway side needs the `/payments/` section especially.

Base URL (local dev): `http://localhost:8000/api/`

---

## 1. Auth

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/auth/register/` | Create a student account |
| POST | `/auth/login/` | Get auth token |
| POST | `/auth/logout/` | Invalidate token |
| GET | `/auth/me/` | Get current logged-in user's profile |
| PATCH | `/auth/me/` | **NEW** — Update editable profile fields |
| POST | `/auth/import/` | **NEW** — Admin: bulk-register students from a CSV roster (dry-run supported) |
| POST | `/auth/claim/` | **NEW** — Imported student sets their first password |
| GET | `/auth/claim-batches/` | **NEW** — Admin: list shared claim codes |
| POST | `/auth/claim-batches/{id}/deactivate/` | **NEW** — Admin: retire a claim code |
| POST | `/auth/reset-code/` | **NEW** — Rep/admin: issue a one-time password-reset code |
| POST | `/auth/reset-password/` | **NEW** — Student sets a new password with that code |
| POST | `/auth/users/{id}/set-role/` | **NEW** — Admin: promote a student to class rep |

**POST /auth/register/**
```json
// Request
{ "username": "jdoe", "email": "jdoe@school.edu.ng", "password": "...",
  "matric_number": "CSC/2021/045", "department_id": 3, "level": "400" }

// Response  201
{ "id": 12, "username": "jdoe", "role": "student", "department": "Computer Science",
  "level": "400" }
```
`level` is new — one of `"100"`, `"200"`, `"300"`, `"400"`, `"500"`.

**Registration hardening (v2.1):**
- A username must not contain `@`; use email or matric number separately at login.
- Duplicate `username`, `email`, or `matric_number` values are checked
  case-insensitively and return the same generic `400` error below — the response
  never reveals which identifier collided.
- Privileged account fields such as `role`, `is_staff`, `is_superuser`, and
  `is_active` are ignored: public registration always creates an active student.
- Privilege fields are also ignored by `PATCH /auth/me/`.

```json
// Response  400 for any duplicate identifier
{ "error": "bad_request", "message": "Unable to register with the provided details." }
```

**POST /auth/login/**
```json
// Request — use exactly one of username, email, or matric_number
{ "username": "jdoe", "password": "..." }
{ "email": "JDOE@school.edu.ng", "password": "..." }
{ "matric_number": "csc/2021/045", "password": "..." }

// Response  200
{ "token": "9f8a3b...", "user": { "id": 12, "username": "jdoe", "role": "student" } }
```
Frontend stores the token and sends it as `Authorization: Token <token>` on every request after this.

**Login identifier rules (v2.1):**
- `email` and `matric_number` are matched case-insensitively.
- `username` remains exact-case and cannot be used as an email-like identifier.
- A missing, invalid, inactive, or unclaimed account returns the same generic
  `400 { "error": "bad_request", "message": "Invalid username or password." }`.

**GET /auth/me/**
```json
// Response  200
{ "id": 12, "username": "jdoe", "email": "jdoe@school.edu.ng", "matric_number": "CSC/2021/045",
  "department": "Computer Science", "full_name": "John Doe", "level": "400",
  "role": "student", "phone_number": "" }
```
`full_name` is read-only. It joins trimmed first and last names; when both are
blank, it falls back to `username`. It is not accepted as a profile-edit field.

**PATCH /auth/me/**  — NEW, fixes a real gap: students had no way to update their own info after registering
```json
// Request (only send fields you're changing)
{ "phone_number": "08012345678", "level": "500" }

// Response  200 → same shape as GET /auth/me/
```
Only `phone_number` and `level` are freely editable. `department_id` may be set
once while it is empty. `username`, `email`, `matric_number`, `department`,
`full_name`, and `role` are read-only; privileged account flags such as
`is_staff`, `is_superuser`, and `is_active` are ignored.

### Roster import + account claiming (NEW)

**Why this exists:** most students pay online (so they self-register), but a department
wants the *complete* class roster on file — otherwise "who hasn't paid" only tracks people
who bothered to sign up, and someone can escape dues simply by never registering.

**POST /auth/import/** (admin only, `multipart/form-data`)
```
fields: file=<students.csv>   dry_run=true|false
```
CSV header — **only `first_name` and `matric_number` are required**. A department's records
often don't include department/level/email, so those columns are optional and the student
completes their profile later:
```csv
first_name,last_name,matric_number,level,department,email
Chidi,Okafor,CSC/2021/045,400,Computer Science,
```
```json
// Response  200
{ "created": 180, "skipped_existing": 12, "errors_total": 8, "dry_run": false,
  "errors": [ { "row": 14, "matric_number": "CSC/2021/014", "errors": ["invalid level: 600"] } ],
  "claim_batch_code": "K7QM4X2P9R" }
```
- `dry_run=true` validates and reports **without writing anything** — run this first.
- Re-uploading a corrected file is safe: existing matric numbers are reported under
  `skipped_existing`, never duplicated.
- Created accounts get **no password at all** — they physically cannot be logged into
  until claimed. Nothing is distributed per student, which is what makes 2,000+ rows
  manageable: the admin shares **one** `claim_batch_code` per class.

**POST /auth/claim/** (public, throttled like login)
```json
// Request
{ "matric_number": "CSC/2021/045", "first_name": "Chidi",
  "batch_code": "K7QM4X2P9R", "password": "...", "email": "chidi@school.edu.ng" }

// Response  200
{ "message": "Account claimed successfully. You can now log in.", "username": "CSC/2021/045" }
```
`email` is optional here (imported rows may not have had one) — the student adds it.
Username is derived from the matric number. **Every** failure before the password check
returns the same generic `{ "error": "claim_failed" }`, so this endpoint can't be used to
probe which matric numbers exist (§8).

**GET /auth/claim-batches/** (admin only) → last 50 batches: `id`, `code`, `is_active`, `created_at`.
**POST /auth/claim-batches/{id}/deactivate/** (admin only) — retire a code once the claim window closes.

### Password reset — "Option A" (NEW, no email dependency)

Deliberate choice: this student population has no reliable email, so a reset is
**assisted but self-service at the password step**. A rep/admin hands over a one-time
code in person; the student sets the new password themselves — the rep never sees it.

**POST /auth/reset-code/** (class rep/admin)
```json
// Request
{ "matric_number": "CSC/2021/045" }

// Response  200
{ "matric_number": "CSC/2021/045", "code": "4X2P9RK7", "expires_in_minutes": 30 }
```
Reps are scoped to their own department (admins reach anyone). Issuing a new code retires
any previous live code for that student, so only one can ever work. Accounts that have not
been claimed yet return `400` — those students use `/auth/claim/` instead.

**POST /auth/reset-password/** (public, throttled like login)
```json
// Request
{ "matric_number": "CSC/2021/045", "code": "4X2P9RK7", "new_password": "..." }

// Response  200  { "message": "Password updated. You can now log in." }
```
Errors: `invalid_code` / `code_expired` / `weak_password`. Codes are **single-use** and
expire after 30 minutes.

### Rep promotion (NEW)

**POST /auth/users/{id}/set-role/** (admin only)
```json
// Request
{ "role": "class_rep" }        // or "student" to demote

// Response  200  { "id": 12, "username": "jdoe", "role": "class_rep" }
```
**Reps are ordinary students until an admin ticks them.** Promotion grants the
mark-paid-offline power, so it can never happen through self-service. This endpoint can
only ever set `student`/`class_rep` (never `admin`), and an admin cannot change their own
role.

---

## 2. Departments

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/departments/` | List all departments (for signup dropdown) |

---

## 3. Contributions

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/contributions/` | List active contributions for the logged-in student's department (and matching level, if set) |
| POST | `/contributions/` | Create a new contribution (class rep/admin only) |
| GET | `/contributions/{id}/` | Get one contribution's detail |
| GET | `/contributions/{id}/summary/` | Total expected vs collected (feeds Data/AI dashboard) |
| GET | `/contributions/{id}/payments/` | **NEW** — Full list of every student's payment status for this contribution (class rep/admin only) |

**GET /contributions/**
```json
// Response  200
[
  { "id": 5, "title": "Departmental Shirt 2026", "amount": "3500.00",
    "deadline": "2026-09-30T23:59:00Z", "is_mandatory": true, "target_level": null,
    "has_paid": false }
]
```
`has_paid` is computed per the logged-in student so the frontend doesn't have to.
`target_level: null` means it applies to every level; otherwise a value like `"400"`
means only that level sees/owes it.

**POST /contributions/**  (class rep/admin only)
```json
// Request — deadline is required; use null for an open-ended collection
{ "title": "Excursion Fee", "description": "...", "amount": "5000.00",
  "deadline": "2026-10-15T23:59:00Z", "is_mandatory": true, "target_level": "400" }

// Response  201 — the GET/list item shape plus department_id
{ "id": 5, "title": "Excursion Fee", "amount": "5000.00",
  "deadline": "2026-10-15T23:59:00Z", "is_mandatory": true, "target_level": "400",
  "has_paid": false, "department_id": 3 }
```
`deadline` must be present: omitting it is `400`; sending an explicit `null`
creates an open-ended contribution. `target_level` is optional — omit or send
`null` for a contribution that applies to the whole department.

Class representatives should not send `department_id`: the backend always uses
their own department. Admins may optionally send it to target another
department; a system admin without a department must do so.

**GET /contributions/{id}/payments/**  — NEW (class rep/admin only)
```json
// Response  200
[
  { "student": "Chidi Okafor", "matric_number": "CSC/2021/045", "status": "success", "paid_at": "2026-09-06T18:20:00Z" },
  { "student": "Ada Bello", "matric_number": "CSC/2021/061", "status": "pending", "paid_at": null }
]
```
This is the gap `outstanding-students` (section 6) didn't cover — that endpoint only lists who
owes money; this one shows everyone's status, paid or not, which a class rep will want for a
full picture.

**POST /contributions/{id}/payments/**  — NEW (class rep/admin only)
Marks a student as paid **without** an online gateway transaction — for when a student has
paid offline (cash, transfer) and the class rep updates the record on their behalf. The
amount is ALWAYS the contribution's amount, set server-side; the student is never asked for
a price.

```json
// Request
{ "matric_number": "CSC/2021/045", "receipt_reference": "RCPT-8842" }

// Response  201
{ "student": "Chidi Okafor", "matric_number": "CSC/2021/045", "status": "success",
  "paid_at": "2026-09-10T12:00:00Z", "method": "manual" }
```

**`receipt_reference` is required** (teller slip / receipt-book / transfer reference).
It is the audit hook that makes an offline mark reconcilable: the Payment row stores both
`receipt_reference` and `recorded_by` (who marked it), so a rep's marks can always be
listed and checked against cash actually banked. Missing it → `400 bad_request`.

The student is notified that the mark happened ("your rep recorded an offline payment of
₦X — report it if this is wrong"), so students police their own records.

Guards, all returning `400`/`403`/`409` before any write:
- the student must be in the **same department** as the contribution, and in the
  contribution's `target_level` when one is set (a bogus row would inflate collected totals);
- a rep **cannot mark themselves** paid (only a real admin may);
- if the student already has a `success` payment (online or manual) for this contribution,
  returns `409 already_paid` — same rule as `/payments/initiate/`.

---

## 4. Payments  ⚠️ most important section for the Payment Gateway person

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/payments/initiate/` | Start a payment — returns gateway checkout URL |
| POST | `/payments/webhook/` | Paystack/Flutterwave calls this automatically — no user, no auth token |
| GET | `/payments/verify/{reference}/` | Manually re-check a payment's status |
| GET | `/payments/history/` | Logged-in student's own payment history |
| GET | `/payments/{id}/receipt/` | Digital receipt for one payment |

**POST /payments/initiate/**
```json
// Request
{ "contribution_id": 5 }

// Response  200
{ "reference": "PSK_8f3a9c2e", "checkout_url": "https://checkout.paystack.com/8f3a9c2e" }
```
Frontend redirects the student to `checkout_url`. That's the entire frontend responsibility for payment — no card handling on our side.

**Duplicate payment handling — NEW, was previously undefined:**
If a student already has a `success` payment for this contribution, `/payments/initiate/`
returns a `409 Conflict` instead of creating a new attempt:
```json
// Response  409
{ "error": "already_paid", "message": "You have already paid for this contribution." }
```
If a previous attempt exists but is `pending` or `failed`, initiating again is allowed —
this creates a new attempt so a failed/abandoned payment doesn't block retrying.

**Amount rule — the settlement gate (applies to BOTH the webhook and `/payments/verify/`)**
The amount charged by the gateway must equal the contribution's amount **exactly**
(compared in integer kobo; the amount is always taken server-side, never from the client).
- exact match → `success`
- **underpayment** → `failed` + `refund_status: pending_review`
- **overpayment** → `failed` + `refund_status: pending_review`
- gateway amount missing/unreadable → `failed` (never credit on trust)
- a charge that would be a **duplicate** success for the same fee → `failed` +
  `refund_status: pending_review` (the student was charged twice; someone must refund one)

The student's notification states which way the amount was wrong, so they know what to do.

**Refunds are NEVER automatic.** A mismatch sets `refund_status` to `pending_review` and
a human reviews it in the Django admin (*Payments → filter by refund status*) before money
moves. `paid_amount` records what the gateway actually took, so the review has the real
figure. Transitions: `none` → `pending_review` → `refunded` / `rejected`. A webhook retry
never overwrites an already-reviewed refund flag (that would risk a double refund).

`/payments/initiate/` also refuses a fee whose `deadline` has already passed. It answers
`404 not_found` — **not** `400` — because an expired fee is *invisible* to students
(the contributions list hides it too), so a stale id can't reveal that the fee
exists or start a payment for a collection nobody is accepting:
```json
// Response  404
{ "error": "not_found", "message": "Contribution not found or not available to you." }
```

**Gateway failure handling (v2.1):**
Request timeouts, non-JSON responses, and unsuccessful or incomplete gateway
responses for `/payments/initiate/` and `/payments/verify/{reference}/` return:
```json
// Response  502
{ "error": "gateway_unavailable", "message": "Payment gateway is unavailable. Try again shortly." }
```
These responses never expose raw gateway payloads or provider exception details.
Logs record only safe operation/status metadata (and the payment reference on
webhook settlement); secrets, authorization headers, and raw payloads are never
logged. Raw webhook payloads remain archived in `payments.Transaction` for audit.

**GET /payments/verify/{reference}/**
The student's own "refresh status" call for a payment *they* started — it never
returns another student's row.
```json
// Response  200  (status is re-checked against the gateway)
{ "message": "Payment verification completed.", "payment": { "…": "PaymentSerializer shape" } }

// Response  404  (unknown reference, or someone else's — no existence leak)
{ "error": "not_found", "message": "Payment not found." }
```
Only a **terminal** gateway outcome changes the row: `success` applies the amount
rule above, `failed`/`reversed` mark the attempt `failed`, and non-terminal
outcomes (`abandoned`, `pending`, `ongoing`, `processing`) leave the payment
exactly as it was — the student just closed the checkout or the charge hasn't
settled yet. Re-checking is always safe to retry.

**POST /payments/webhook/**  (called by the gateway, not the frontend)
```json
// Incoming payload (Paystack shape, example)
{ "event": "charge.success", "data": { "reference": "PSK_8f3a9c2e", "amount": 350000, "status": "success" } }

// Response  200  { "received": true }
```
Backend verifies the signature, matches `reference` to a `Payment` row **by exact reference
string only** (never "latest pending payment for this student"), applies the amount rule
above, and fires a `Notification`. Invalid signature → `400`, no processing. Malformed body
(non-JSON, non-dict, or a `data` block that isn't an object) → `400`.

**Idempotent by design:** a webhook for a payment that is already `success` returns
`{"received": true}` and changes nothing, so Paystack's retries can never double-credit a
student.

**Webhook proof record:** the first delivery for each reference is archived
verbatim in `payments.Transaction` (`payment`, `reference`, `raw_payload`,
`received_at`). Retries do not create duplicate proof rows; unknown references
are still retained with `payment: null` for forensics.

**GET /payments/history/**
```json
// Response  200
[
  { "id": "uuid...", "contribution": "Departmental Shirt 2026", "amount": "3500.00",
    "status": "success", "verified_at": "2026-09-06T18:20:00Z" }
]
```

---

## 5. Notifications

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/notifications/` | List logged-in user's notifications |
| POST | `/notifications/{id}/read/` | Mark one as read |

**Note:** there is deliberately no `POST /notifications/` to create one. Notifications are
system-generated only (fired automatically on payment success/failure and new contribution
creation, per `AGENTS.md`) — frontend never creates these directly.

**GET /notifications/**
```json
// Response  200
[
  { "id": 18, "notification_type": "payment_success",
    "message": "Your payment of ₦3,500.00 for \"Departmental Shirt 2026\" was successful.",
    "contribution": 5, "contribution_title": "Departmental Shirt 2026",
    "is_read": false, "created_at": "2026-09-20T12:00:00Z" }
]
```
This is the safe serializer shape for both list and mark-read responses.
`recipient` and other internal/account fields are deliberately not exposed.

**POST /notifications/{id}/read/** returns the same safe shape with `is_read: true`.

---

## 6. Analytics (feeds the Data/AI person's dashboard)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/analytics/collection-stats/` | Total collected vs outstanding, per contribution/department |
| GET | `/analytics/outstanding-students/?contribution_id=5` | List of students who haven't paid a given contribution |

**GET /analytics/collection-stats/**
```json
// Response  200
{ "total_expected": "175000.00", "total_collected": "122500.00", "outstanding_count": 15 }
```
This is the raw data source for any AI assistant feature (e.g. "how much have we collected?") — the assistant layer just queries this endpoint and phrases the answer in natural language, no separate data pipeline needed.

---

## 7. Error responses — NEW, standardized across every endpoint

Every error follows this shape, so the frontend only needs one error-handling pattern:
```json
{ "error": "short_machine_code", "message": "Human-readable explanation" }
```

| Status | When | `error` value |
|---|---|---|
| 400 | Bad request — missing/invalid fields, invalid webhook signature | `bad_request` |
| 400 | Request body could not be parsed as JSON | `parse_error` |
| 401 | Missing or invalid auth token — deliberately indistinguishable | `unauthorized` |
| 403 | Logged in, but not allowed (e.g. a student trying to create a contribution) | `permission_denied` |
| 404 | Resource doesn't exist **or isn't visible to this user** (e.g. bad contribution ID) | `not_found` |
| 405 | Wrong HTTP method for that route | `method_not_allowed` |
| 409 | Conflict — e.g. duplicate payment attempt (see section 4) | `conflict` |
| 415 | Body sent with an unsupported `Content-Type` (send `application/json`) | `unsupported_media_type` |
| 429 | Rate limited — login/register are throttled server-side at 10/min per IP | `throttled` |
| 502 | Upstream payment gateway unavailable, malformed, or unsuccessful | `gateway_unavailable` |
| 500 | Unexpected server error | **no JSON shape** — Django's own error page (no custom `handler500` exists yet). Show a generic "something went wrong, try again" and log the status. |

A few endpoints return a **domain-specific** code that is more useful than the generic
one. Branch on `payload.error` when you can; the status code is always authoritative:

| Status | `error` | Where | What the UI should do |
|---|---|---|---|
| 409 | `already_paid` | `/payments/initiate/`, `POST /contributions/{id}/payments/` | The fee is already settled — hide "Pay now", refresh the row |
| 400 | `claim_failed` | `/auth/claim/` | One generic message; the API never says which of matric/first name/code was wrong |
| 400 | `weak_password` | `/auth/claim/`, `/auth/reset-password/` | Show the password rules returned in `message` |
| 400 | `invalid_code` / `code_expired` | `/auth/reset-password/` | Codes are single-use and time-boxed — ask for a new one |
| 400 | `email_taken` | `/auth/claim/` | Ask the student to pick a different email |
| 403 | `forbidden` | `POST /contributions/{id}/payments/` | A rep may not mark *themselves* paid — only a real admin may |
| 503 | `unavailable` | `POST /contributions/{id}/payments/` | Offline mark-paid isn't possible for this fee/student |

**Rules of thumb for the single handler:** branch on `payload.error`, fall back to the
status code, and always render `payload.message`. Every error below 500 — including the
gateway webhook's rejections — carries both keys, so `message` is never missing.

---

## Notes for the team
- All endpoints except `register`, `login`, and `webhook` require the `Authorization: Token <token>` header.
- Admin/class-rep-only endpoints are marked above — backend enforces this via role checks (returning `403` per section 7), frontend just needs to hide those UI actions for regular students.
- Dates are ISO 8601 UTC. Amounts are strings to avoid floating-point rounding issues — display as-is, don't parse as float.

## Open questions for the team (not built yet — flagging instead of guessing)
- **Password reset** — no forgot-password flow exists yet. Worth deciding if this is in scope for the 16 days or explicitly cut for the demo.
- **Token expiry** — tokens currently don't expire. Fine for a hackathon demo; flag if the team wants otherwise.
- **Pagination** — list endpoints (`/contributions/`, `/payments/history/`, `/notifications/`) return everything with no paging. Fine at hackathon scale; would need revisiting for a real deployment.
- **`500` has no JSON body** — DRF's `EXCEPTION_HANDLER` covers *handled* API errors only; an unhandled exception falls through to Django's own HTML error page, so a genuine 500 is the one status the frontend's single error handler can't read a `message` from. No `handler500` is registered today. The frontend already falls back to a generic "something went wrong" (see `FRONTEND_LINKING.md`), so this is a polish item, not a blocker — but it's the reason the error contract is stated as "every error **below** 500".

---

## 8. Security & authorization rules — binding (added 2026-09-07, security audit)

These apply to every endpoint above. Backend must enforce them; QA must test them.

**Object-level authorization (no IDOR):**
- `/payments/verify/{reference}/`, `/payments/{id}/receipt/`, `/payments/history/` — a student may only access **their own** payments. Class reps/admins may access payments within their own department.
- `/notifications/{id}/read/` — only the notification's owner may mark it read; return `404` (not `403`) for other users' notifications so existence isn't leaked.
- `/contributions/{id}/payments/` — class rep/admin only, scoped to their own department.

**Payment webhook (`/payments/webhook/`) — in addition to signature verification:**
- **Amount check:** the verified `data.amount` (kobo ÷ 100) must equal the matched `Payment`'s amount. On mismatch, do NOT mark success — mark the payment `failed` and log it.
- **Idempotency:** duplicate deliveries of the same `charge.success` must be safe no-ops returning `200`. Never create a second `Payment`/`Transaction` for the same reference. Process webhooks inside a DB transaction.
- Verify the HMAC against the **raw request body** (never re-serialized JSON).

**Analytics (section 6) — permissions decided:** both `/analytics/` endpoints are **class rep/admin only** (`403` for students). The Data/AI teammate consumes them via a dedicated read-only service account with the class-rep role — never from the browser.

**Registration hardening (section 1):** passwords are validated with Django's built-in validators (min 8 chars, common-password and all-numeric checks). Duplicate `username`, `email`, or `matric_number` attempts return one **generic** error (no account enumeration), with case-insensitive identifier checks; usernames cannot contain `@`. Privileged account fields are ignored on registration and profile updates. Login and register are rate-limited server-side at 10/min per IP — clients must handle `429` using the standard error shape.
