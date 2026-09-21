# TEST_REPORT.md — Agent 5 (QA) Contract Verification
**Date:** 2026-09-16 (updated after the settlement/audit sprint) · **Suite at report time:** 152 tests, all passing (`manage.py test` → OK). *Current suite: 222 tests (Phase 3 added contribution edit/close + analytics); per-endpoint verdicts below are unchanged — every listed shape still holds.*
**Scope:** every endpoint in `API_CONTRACT.md` v2 — status codes + documented response shapes — plus the settlement, audit and roster flows added this sprint. Per `AGENTS.md`, mismatches were flagged; after owner review, M-1/M-2/M-3/M-5/M-6 were **fixed to match the contract** and their tests updated together; M-4 was resolved by owner decision (deferred to the Data/AI teammate, handover in `docs/ANALYTICS_INTEGRATION.md`).

## Per-endpoint verdicts

| § | Endpoint | Built | Status | Documented shape | Verdict |
|---|---|---|---|---|---|
| 1 | POST `/auth/register/` | ✅ | 201 | `{id, username, role, department, level}` | ✅ matches |
| 1 | POST `/auth/login/` | ✅ | 200 | `{token, user:{id, username, role}}` | ✅ matches |
| 1 | POST `/auth/logout/` | ✅ | 200 | (unspecified) | ✅ token invalidated after |
| 1 | GET `/auth/me/` | ✅ | 200 | `{id, username, email, matric_number, department, level, role, phone_number}` | ✅ matches |
| 1 | PATCH `/auth/me/` | ✅ | 200 | same shape; only `phone_number`/`level` editable | ✅ matches (`matric_number` correctly read-only) |
| 1 | POST `/auth/import/` | ✅ | 201 | (unspecified) | ✅ `dry_run` + per-row errors; inert accounts (`set_unusable_password`) |
| 1 | POST `/auth/claim/` | ✅ | 200/400 | (unspecified) | ✅ matric + first name + batch code; sets own password |
| 1 | GET `/auth/claim-batches/` | ✅ | 200/403 | (unspecified) | ✅ admin only |
| 1 | POST `/auth/claim-batches/{id}/deactivate/` | ✅ | 200/403 | (unspecified) | ✅ admin only; retired code stops working |
| 1 | POST `/auth/reset-code/` | ✅ | 201/403 | (unspecified) | ✅ rep/admin only; single-use, expiring code |
| 1 | POST `/auth/reset-password/` | ✅ | 200/400 | (unspecified) | ✅ replay, expiry and wrong-matric all rejected |
| 1 | POST `/auth/users/{id}/set-role/` | ✅ | 200/400/403 | (unspecified) | ✅ admin only; never grants `admin`; no self-change |
| 2 | GET `/departments/` | ✅ | 200 | (unspecified) | ✅ `{id, name, faculty}` locked |
| 3 | GET `/contributions/` | ✅ | 200 | `{id, title, amount, deadline, is_mandatory, target_level, has_paid}` | ✅ matches; amount is a string |
| 3 | POST `/contributions/` | ✅ | 201/403 | same shape | ✅ matches (students → 403 `{error, message}`) |
| 3 | GET `/contributions/{id}/` | ✅ | 200 | list shape | ✅ matches |
| 3 | GET `/contributions/{id}/summary/` | ✅ | 200 | "expected vs collected" | ✅ `{total_expected, total_collected, outstanding_count}`, money as strings |
| 3 | GET `/contributions/{id}/payments/` | ✅ | 200/403 | `[{student, matric_number, status, paid_at}]` | ✅ matches |
| 3 | POST `/contributions/{id}/payments/` | ✅ | 201/409 | `{student, matric_number, status, paid_at, method}` | ✅ matches (`method: "manual"`, duplicate → `409 already_paid`) |
| 4 | POST `/payments/initiate/` | ✅ | 200/409 | `{reference, checkout_url}` | ✅ matches (superset: keeps `message`, `payment`, legacy `authorization_url`) |
| 4 | POST `/payments/webhook/` | ✅ | 200/400 | `{"received": true}` | ✅ matches on every return path |
| 4 | GET `/payments/verify/{reference}/` | ✅ | 200 | (unspecified) | ✅ `{message, payment:{…}}` locked |
| 4 | GET `/payments/history/` | ✅ | 200 | `[{id, contribution(title), amount, status, verified_at}]` | ✅ matches |
| 4 | GET `/payments/{id}/receipt/` | ✅ | 200/404 | (unspecified) | ✅ PaymentSerializer shape locked |
| 5 | GET `/notifications/` | ✅ | 200 | (unspecified) | ✅ shape locked (see prior flag) |
| 5 | POST `/notifications/{id}/read/` | ✅ | 200/404 | (unspecified) | ✅ idempotent; foreign rows → 404 (not 403) per §8 |
| 6 | GET `/analytics/collection-stats/` | ❌ | — | `{total_expected, total_collected, outstanding_count}` | ❌ **NOT BUILT** — deferred to the Data/AI teammate by owner decision (M-4) |
| 6 | GET `/analytics/outstanding-students/?contribution_id=` | ❌ | — | list | ❌ **NOT BUILT** — deferred to the Data/AI teammate by owner decision (M-4) |

## Mismatch resolutions (2026-09-11/16 — owner-approved fixes, all verified by tests)

- **M-1 — RESOLVED.** `/payments/initiate/` now returns the contract keys `reference` (top-level) and `checkout_url`, while keeping `message`, `payment`, and the legacy `authorization_url` for backward compatibility — a safe superset of the contract. Verified by `test_initiate_matches_contract_shape`.
- **M-2 — RESOLVED.** Every webhook return path now responds exactly `{'received': True}` per §4. Verified by `test_webhook_matches_contract_shape`.
- **M-3 — RESOLVED.** `PaymentSerializer` now returns `contribution` as the fee **title** and adds `verified_at` (set when a payment reaches `success`, null otherwise) — history/receipt/verify are all consistent. Contract amendment note: `id` stays an **integer** (the contract's `uuid` example was illustrative; changing the PK type was rightly out of scope). Verified by `test_history_matches_contract_shape` + `test_history_pending_has_null_verified_at`.
- **M-4 — RESOLVED BY OWNER DECISION (not built).** §6 analytics stays out of backend-team scope so the Data/AI teammate can build it in their own app. Handover: `docs/ANALYTICS_INTEGRATION.md` documents the sanctioned read-model helpers (`Contribution.total_expected/total_collected/outstanding_count/eligible_students`, `payments_bridge`), the §8 permission rules (`IsClassRepOrAdmin`, service account), the §6 response shapes, and mutual sandboxing rules (no cross-app source edits; signals/bridge patterns only).
- **M-5 — RESOLVED.** Registration no longer attaches DRF's auto `UniqueValidator` (username/email/matric are declared explicitly); ALL duplicates — exact-case or not, username included — raise one generic, field-anonymous non-field error: `"Unable to register with the provided details."`. DB unique constraints remain as the fail-safe backstop. Verified by `test_duplicate_registration_exact_case_is_generic`, `test_duplicate_registration_case_insensitive_is_generic`, `test_duplicate_registration_username_is_generic`.
- **M-6 — RESOLVED.** The custom exception handler no longer prefixes `non_field_errors` with a field name, so the generic duplicate message stays truly anonymous; genuine field errors keep their useful `field: message` prefix. Verified by the M-5 tests above (exact-message assertions).

## Post-fix payments re-review (crash-path hardening, 4 new guards + regression tests)
- Non-JSON gateway body on initiate/verify → clean `502 gateway_unavailable` (was: unhandled JSONDecodeError → 500).
- `status: true` payload missing `data` → clean 400 instead of `KeyError` → 500.
- Webhook body that is unparseable JSON or non-dict → clean 400 (was: 500).
- Non-numeric `contribution_id` on initiate → clean 400 (was: ORM ValueError → 500).
- Verified by `test_initiate_with_malformed_gateway_response_is_502`, `test_initiate_with_non_numeric_contribution_id_is_400`, `test_verify_with_malformed_gateway_response_is_502`.

## Prior flags still open (see `directives/BOTTLENECKS.md`)
- `Transaction` model (webhook raw-payload proof) specified by `AGENTS.md`/`BACKEND_DB_STRUCTURE.md` but never built — owner decision pending.
- §5 Notifications response shape unspecified in the contract (shipped as `notification_type` — frontend to confirm).

## §8 security-rule coverage (QA-verified)
Token auth 401s ✅ · standard `{error, message}` shape ✅ · login throttled → 429 ✅ · IDOR: payments history/receipt/verify owner-only ✅ · notifications read owner-only + 404-not-403 ✅ · roster/manual-mark rep-or-admin + department-scoped ✅ · webhook: signature (400 on bad/missing), amount check (mismatch → failed), idempotent duplicate deliveries ✅ · kobo conversion (×100) asserted ✅.

**Test locations:** `apps/<app>/tests.py` (behaviour) + `apps/<app>/tests_contract.py` (contract shapes, this pass). Where a mismatch is flagged, the contract test locks **current** behaviour and is named `*_flagging_*`/`*_FLAGGED` so the owner's fix will intentionally break it and force the doc/test update together.
