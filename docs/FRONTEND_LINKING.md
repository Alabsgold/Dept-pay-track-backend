# FRONTEND_LINKING.md — How the frontend connects to the backend
Team Visionary Coders — build against this file. It is the single map from
frontend screens/actions to backend endpoints. If a button you need is not in
this table, ASK before building a new endpoint — do not invent routes.

## Global rules (apply to every request)
1. Send `Authorization: Token <token>` on every request EXCEPT register, login and webhook.
2. Every error has the same shape: `{ "error": "code", "message": "text" }` — write ONE error handler.
3. Money is always a string (`"3500.00"`). Never parse it as float in JavaScript; display as-is.

## Endpoint map
"Needed by" = the frontend page/action that calls it.

### Auth
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| POST | /auth/register/ | No | {username,email,password,matric_number,department_id,level} | 201 {id,username,role,department,level} | Sign-up page |
| POST | /auth/login/ | No | {username,password} | 200 {token,user} | Login page (store token) |
| POST | /auth/logout/ | Yes | — | 200 {message} | Logout button |
| GET | /auth/me/ | Yes | — | 200 profile | Navbar / profile prefill |
| PATCH | /auth/me/ | Yes | {level,phone_number} | 200 profile | Edit profile page |

Rate limits: login 10/min, register 10/min (per IP). If you get 429, wait a minute — don't hammer.

### Departments
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /departments/ | No | — | 200 [{id,name,faculty}] | Sign-up dropdown |

### Contributions
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /contributions/ | Yes | — | 200 [...] | Student fees page; Rep fees page |
| POST | /contributions/ | Yes (rep/admin) | {...} | 201 [...] | Rep "new fee" form |
| GET | /contributions/{id}/ | Yes | — | 200 detail | Fee detail page |
| GET | /contributions/{id}/summary/ | Yes | — | 200 {total_expected,total_collected,outstanding_count} | Dashboard cards; AI/Analytics |
| GET | /contributions/{id}/payments/ | Yes (rep/admin) | — | 200 per-student list | Rep roster view |
| POST | /contributions/{id}/payments/ | Yes (rep/admin) | {matric_number} | 201 | Rep "mark as paid" (offline payment) |

### Payments
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| POST | /payments/initiate/ | Yes | {contribution_id} | 200 {checkout_url} | "Pay now" button → redirect to checkout_url |
| GET | /payments/history/ | Yes | — | 200 [...] | "My payments" page |
| GET | /payments/verify/{reference}/ | Yes | — | 200 | Manual "refresh status" |
| GET | /payments/{id}/receipt/ | Yes | — | 200 | Receipt modal |
| POST | /payments/webhook/ | No (gateway) | signed payload | 200 | n/a — server-to-server, never called by the frontend |

### Notifications (coming — next module)
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /notifications/ | Yes | — | 200 [...] | Bell icon dropdown + unread badge |
| POST | /notifications/{id}/read/ | Yes | — | 200 | Click notification |

### Analytics
| Method | Path | Auth | Body | Success | Needed by |
|---|---|---|---|---|---|
| GET | /analytics/collection-stats/ | Yes (rep/admin) | — | 200 {total_expected,total_collected,outstanding_count} | Analytics dashboard |
| GET | /analytics/outstanding-students/?contribution_id=5 | Yes (rep/admin) | — | 200 [...] | "Who still owes" widget |

## Endpoints that are NOT the frontend's job
- POST /payments/webhook/ — called by Paystack's server only. Never call it from the browser.

## Common screen → backend map (things that trip people up)
| Screen / action | Backend call(s) |
|---|---|
| "Pay now" → redirect to Paystack | POST /payments/initiate/ {contribution_id} → take checkout_url → redirect; on return, re-GET /contributions/ and rely on has_paid |
| Student dashboard vs Rep dashboard "same fees list" | Same GET /contributions/; backend already hides/shows by role — just render by user.role |
| Rep "mark someone paid" vs "see roster" | Same route: GET /contributions/{id}/payments/ (list) and POST /contributions/{id}/payments/ (action) |
| Unread badge | GET /notifications/ → count items where is_read == false |
| Amounts | Always strings "3500.00". Display as-is. |