# FRONTEND_LINKING.md — How the frontend connects to the backend
Team Visionary Coders — build against this file. It is the single map from
frontend screens/actions to backend endpoints. If a button you need is not in
this table, ASK before building a new endpoint — do not invent routes.

> **Status:** everything in this file is implemented, tested and deployed-ready.
> Exact request/response bodies live in [`API_CONTRACT.md`](./API_CONTRACT.md);
> this file is the wiring map plus the integration checklist.

## Global rules (apply to every request)
1. Send `Authorization: Token <token>` on every request EXCEPT register, login, claim, reset-password, departments and webhook.
2. Every error has the same shape: `{ "error": "code", "message": "text" }` — write ONE error handler. **Never** read `detail`, `non_field_errors` or `errors` in the frontend; the backend normalizes all of them (see §Error handling).
3. Money is always a string (`"3500.00"`). Never parse it as float in JavaScript; display as-is.
4. Dates are ISO 8601 UTC.
5. `Content-Type: application/json` on every POST/PATCH except the CSV import (multipart) and the webhook (server-only).

## Endpoint map
"Needed by" = the frontend page/action that calls it.

### Auth
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| POST | /auth/register/ | No | {username,email,password,matric_number,department_id,level} | 201 {id,username,role,department,level} | Sign-up page |
| POST | /auth/login/ | No | {password, **one of** username / email / matric_number} | 200 {token,user} | Login page (store token) |
| POST | /auth/logout/ | Yes | — | 200 {message} | Logout button |
| GET | /auth/me/ | Yes | — | 200 profile (incl. read-only `full_name`) | Navbar / profile prefill |
| PATCH | /auth/me/ | Yes | {level, phone_number, department_id?} | 200 profile | Edit profile page |
| POST | /auth/import/ | Yes (admin) | multipart `file` = CSV | 200 {created,skipped_existing,errors_total,errors[]} | Admin roster upload |
| POST | /auth/claim/ | No | {matric_number,first_name,batch_code,password,email?} | 200 {message,username} | Imported student's first login |
| GET | /auth/claim-batches/ | Yes (rep/admin) | — | 200 [{id,code,is_active,…}] | Admin: active sign-up codes |
| POST | /auth/claim-batches/{id}/deactivate/ | Yes (rep/admin) | — | 200 | Admin: close a sign-up window |
| POST | /auth/reset-code/ | Yes (rep/admin) | {matric_number} | 200 {code,…} | Rep assists a locked-out student |
| POST | /auth/reset-password/ | No | {matric_number,code,password} | 200 {message} | Student sets a new password |
| POST | /auth/users/{id}/set-role/ | Yes (admin) | {role} | 200 {…} | Admin: promote a class rep |

Rate limits: login, register, claim and reset are throttled at 10/min per IP. On
`429` show the returned `message` and stop retrying — don't hammer.

**Login identifier routing — read this before building the login form.**
`/auth/login/` is one endpoint with three optional identifier fields, and the
**key you use must match the type of value** — you cannot put any string in any
field. So a single "Email, matric or username" input needs this on the frontend:

```js
const body = { password };
if (identifier.includes('@'))        body.email = identifier;          // case-insensitive
else if (identifier.includes('/'))   body.matric_number = identifier;  // case-insensitive
else                                 body.username = identifier;       // exact case
```

- `email` is validated as a real email, so a matric number sent there is a `400`.
- `matric_number` is matched case-insensitively.
- `username` is matched **exactly** (case-sensitive) — that is deliberate; don't
  lowercase it before sending.
- Sending none of the three, or a value that doesn't resolve, gives the same
  generic `400 bad_request "Invalid username or password."` — show it verbatim.

**What `PATCH /auth/me/` actually accepts:** `level`, `phone_number`, and
`department_id` (only while it's still empty — "fill once", to stop a student
switching out of a department that is collecting dues from them). `username`,
`email`, `matric_number`, `role` and `full_name` are read-only and silently
ignored on write; don't build editable inputs for them.


### Departments
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /departments/ | No | — | 200 [{id,name,faculty}] | Sign-up dropdown |

### Contributions
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /contributions/ | Yes | — | 200 [...] | Student fees page; Rep fees page |
| POST | /contributions/ | Yes (rep/admin) | {title,amount,deadline,is_mandatory?,target_level?,description?,department_id?} | 201 (incl. server-chosen `department_id`) | Rep "new fee" form |
| GET | /contributions/{id}/ | Yes | — | 200 detail | Fee detail page |
| GET | /contributions/{id}/summary/ | Yes | — | 200 {total_expected,total_collected,outstanding_count} | Dashboard cards; AI/Analytics |
| GET | /contributions/{id}/payments/ | Yes (rep/admin) | — | 200 per-student list | Rep roster view |
| POST | /contributions/{id}/payments/ | Yes (rep/admin) | {matric_number,receipt_reference} | 201 {student,matric_number,status,paid_at,method} | Rep "mark as paid" (offline payment) |

`deadline` is **required** on create (explicit `null` is allowed and means "no deadline").
Don't send `department_id` as a rep — the backend sets it from your profile.

### Payments
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| POST | /payments/initiate/ | Yes | {contribution_id} | 200 {reference,checkout_url,authorization_url,payment} | "Pay now" button → redirect to checkout_url |
| GET | /payments/history/ | Yes | — | 200 [...] | "My payments" page |
| GET | /payments/verify/{reference}/ | Yes | — | 200 {message,payment} | Manual "refresh status" after the Paystack redirect |
| GET | /payments/{id}/receipt/ | Yes | — | 200 payment | Receipt modal |
| GET | /payments/unverified/ | Yes (admin) | — | 200 [...] | Admin refund-review queue |
| POST | /payments/webhook/ | No (gateway) | signed payload | 200 {received:true} | n/a — server-to-server, never called by the frontend |

Use `checkout_url` (contract §4) for the redirect — `authorization_url` is the same
value kept for older integrations. After the student returns from Paystack, either
re-`GET /contributions/` (rely on `has_paid`) or call
`/payments/verify/{reference}/` for a fresh gateway check.

Two `404 not_found` cases to render as a normal "not found" state, not a crash:
- **An expired fee.** `/payments/initiate/` answers `404 not_found` for a fee whose
  `deadline` has passed — students never see it in `/contributions/` either, so a
  stale link/id must not start a payment for a closed collection.
- **An unknown or foreign `reference`** on `/payments/verify/{reference}/`. It never
  leaks whether someone else's payment exists. Verify is safe to retry.

### Notifications
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /notifications/ | Yes | — | 200 [...] | Bell icon dropdown + unread badge |
| POST | /notifications/{id}/read/ | Yes | — | 200 | Click notification |

Only the owner may mark a notification read — another user's id returns `404`, not
`403`, so the UI must treat "not found" here as "not mine".

### Departments
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /departments/ | No | — | 200 [{id,name,faculty}] | Sign-up dropdown (safe to call before login) |

### Analytics (Data/AI teammate's app — not built yet)
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /analytics/collection-stats/ | Yes (rep/admin) | — | 200 {total_expected,total_collected,outstanding_count} | Analytics dashboard |
| GET | /analytics/outstanding-students/?contribution_id=5 | Yes (rep/admin) | — | 200 [...] | "Who still owes" widget |

**Not available yet** — don't wire these into a screen until the Data/AI teammate
ships them. Per-contribution numbers are already available today from
`GET /contributions/{id}/summary/`.

### Ops
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /health/ | No | — | 200 | Platform probe; ping before a live demo (free-tier cold start) |

## Endpoints that are NOT the frontend's job
- POST /payments/webhook/ — called by Paystack's server only. Never call it from the browser.


## Error handling — one handler, exact codes
The backend normalizes **every** error into `{ "error": "code", "message": "text" }`.
It does this for you: DRF's `detail`, serializer `{field: [...]}` dicts and bare
lists are all rewritten before they leave the server (see `backend/core/exceptions.py`).
So your handler is:

```js
const res = await fetch(url, options);
if (!res.ok) {
  const payload = await res.json().catch(() => null);
  // 500 is the one case with no JSON body (Django's own error page).
  const message = payload?.message ?? 'Something went wrong. Please try again.';
  showError(message);           // branch on payload?.error for special cases
  return;
}
```

| Status | `error` | Meaning — what to show / do |
|---|---|---|
| 400 | `bad_request` | Missing/invalid fields. Show `message` next to the form. |
| 400 | `parse_error` | The request body wasn't valid JSON — almost always a frontend bug (`JSON.stringify` the body). |
| 401 | `unauthorized` | Missing or invalid token. Clear the stored token and send to login. |
| 403 | `permission_denied` | Logged in but not allowed. Prefer hiding the button; show `message` if reached. |
| 404 | `not_found` | Doesn't exist **or isn't yours/your department's**. Show a "not found" state. |
| 405 | `method_not_allowed` | A frontend bug (wrong verb) — log it. |
| 409 | `conflict` | Duplicate payment attempt. Refresh the fee and hide "Pay now". |
| 415 | `unsupported_media_type` | Wrong `Content-Type` — send `application/json` (except the multipart CSV import). |
| 429 | `throttled` | Rate-limited (auth routes, 10/min per IP). Show `message`; don't auto-retry. |
| 502 | `gateway_unavailable` | Paystack is down/misbehaving. Offer a retry button — no money moved. |
| 500 | *(no body)* | Unexpected. Show a generic message and log the status. |

Domain-specific codes you may want to branch on (same shape, same status rules):

| Status | `error` | Where | What to do |
|---|---|---|---|
| 409 | `already_paid` | /payments/initiate/, POST /contributions/{id}/payments/ | Fee already settled — refresh instead of paying |
| 400 | `claim_failed` | /auth/claim/ | One generic message — the API deliberately won't say which detail was wrong |
| 400 | `weak_password` | /auth/claim/, /auth/reset-password/ | Show the password rules from `message` |
| 400 | `invalid_code` / `code_expired` | /auth/reset-password/ | Codes are one-time — ask the rep for a fresh one |
| 400 | `email_taken` | /auth/claim/ | Ask for a different email |
| 403 | `forbidden` | POST /contributions/{id}/payments/ | A rep can't mark *themselves* paid — only a real admin can |
| 503 | `unavailable` | POST /contributions/{id}/payments/ | Offline mark-paid can't be honoured for this fee/student — tell the rep to retry or use the gateway |

**Two things that commonly break integrations:**
- On `401`, don't guess whether the token was missing or expired — treat both the same.
- Never read `payload.detail` or `payload.non_field_errors`; those keys do not exist in
  responses anymore.

## Common screen → backend map (things that trip people up)
| Screen / action | Backend call(s) |
|---|---|
| "Pay now" → redirect to Paystack | POST /payments/initiate/ {contribution_id} → take `checkout_url` → redirect; on return, re-GET /contributions/ and rely on `has_paid` |
| Student dashboard vs Rep dashboard "same fees list" | Same GET /contributions/; backend already hides/shows by role — just render by `user.role` |
| Rep "mark someone paid" vs "see roster" | Same route: GET /contributions/{id}/payments/ (list) and POST /contributions/{id}/payments/ (action) |
| Unread badge | GET /notifications/ → count items where `is_read == false` |
| Amounts | Always strings "3500.00". Display as-is. Never `parseFloat` for display |
| "Who still owes" today | Not built. For one fee use GET /contributions/{id}/payments/ and count rows with `status:"pending"` |
| Money is a string, but `has_paid` is a boolean | Never string-compare `amount`; compare `paid` state with `has_paid` only |

## Integration readiness checklist
Backend side is done; tick these off as the frontend wires up.

- [ ] **Base URL** comes from an env var, e.g. `VITE_API_URL` / `NEXT_PUBLIC_API_URL` — never hardcoded. Local: `http://localhost:8000/api`.
- [ ] **Token storage** — `POST /auth/login/` returns `token`; send it as `Authorization: Token <token>` (the literal word `Token`, not `Bearer`).
- [ ] **One error handler** built per the table above; no `detail`/`non_field_errors` reads anywhere.
- [ ] **401 → logout.** Clear the token and route to login; don't try to refresh (tokens don't expire, they're just absent).
- [ ] **Login identifier routed to the right key** (`email` / `matric_number` / `username`) per the rule above — a matric number sent as `email` is a `400`.
- [ ] **`PATCH /auth/me/` only edits** `level`, `phone_number`, `department_id` (fill-once) — the rest are read-only.
- [ ] **`has_paid` drives the fee UI.** It's on every contribution row; don't infer from `/payments/history/`.
- [ ] **Money rendered as a string**, dates via ISO 8601 parsing to local time.
- [ ] **Role gates are cosmetic.** Hide rep/admin buttons by `user.role`, but expect a `403` cleanup call if someone bypasses the UI.
- [ ] **Rate limits respected** — no retry loops on login/register/claim/reset.
- [ ] **Paystack return URL** re-checks status (`GET /contributions/` or `/payments/verify/{reference}/`) rather than trusting the redirect.
- [ ] **`502` handled as retryable** — no money moved; show "try again shortly".
- [ ] **`404` on a fee or a reference treated as a state**, not a crash — expired fees and unknown/foreign references both land here.
- [ ] **`429` and `502` messages shown verbatim** from `message`; both are written to be read by students.
- [ ] **Ping `/health/`** before a live demo (free-tier Render sleeps after ~15 min).
