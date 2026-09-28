# BMONI Embedded — sandbox runbook (verified)

**Purpose:** how to talk to the BMONI Embedded banking API from this backend
**today**, with the shared public sandbox key, and exactly where that walks into
a wall.

**Verified:** 26 September 2026, against `https://embedded-dev.bmoni.com`, using
the docs' shared sandbox key. Every status code below is a real response, not a
reading of the documentation. Re-run the recipe in
[§3](#3-copy-paste-walkthrough-verified) to confirm it yourself.

**Context:** BMONI is the layer that gives each department its own **real NGN
virtual bank account** (money physically lands there). Paystack stays what it is
today — collections for student contributions.

---

## 1. Verified working with the shared sandbox key

| # | Call | Result | What it proves |
|---|---|---|---|
| 1 | `GET /v1/health` | **200** | Base URL reachable |
| 2 | `GET /v1/users` **without** `x-api-key` | **401** `{"message":"Unauthorized"}` | The key is really being checked |
| 3 | `GET /v1/users` with the key | **200** | Key is **partner-scoped** (returns partner `BMONI Hackathon` and its users) |
| 4 | `POST /v1/users` | **201** → `{"user":{"bmoniUserId":"f2989b43-…"}}` | **We can create users** — the whole chain is reachable |
| 5 | `PATCH /v1/users/{id}/kyc` | **200** | KYC profile writes land |
| 6 | `GET /v1/users/{id}/kyc` | **200** | Profile reads back, BVN echoed (`95888168924`) |
| 7 | `POST /v1/users/{id}/onboarding/start-nigeria` | **200** | `{"workflowId":"onboarding-…","isNigeria":true,"status":{"hasBvn":true,"hasLocalWallet":true,…}}` |
| 8 | `GET /v1/users/{id}/bank-accounts/deposit-accounts` | **200** | **A real NGN account number is issued** (see below) |
| 9 | `GET /v1/webhooks/config` | **200** | A partner-scoped webhook subscription already exists, belonging to someone else (see §2) |
| 10 | `GET /v1/users/{id}/smart-wallets/account/balances` | **400** | No wallet yet — wallet creation is the one gated step (§2) |

### The headline result

Step 8 returned a **dedicated, person-named NGN virtual account** for the
department account holder:

```json
{
  "nigerianAccounts": [{
    "id": "4c9342a0-1289-4b6e-a9b7-7af73c7469be",
    "accountName": "Dillon Bunch",
    "bankName": "PROVIDUS BANK",
    "accountNumber": "9845221370",
    "bankCode": "000023",
    "currency": "NGN",
    "targetCurrency": "NGN"
  }],
  "activationAccounts": [{
    "id": "pooled-vba-1",
    "accountName": "Bkey Limited",
    "bankName": "9 Payment Service Bank",
    "accountNumber": "6177463833",
    "bankCode": "XXXXXXX",
    "currency": "NGN",
    "targetCurrency": "EUR"
  }],
  "europeanAccounts": [], "usaAccounts": [], "mexicanAccounts": []
}
```

**This is the product feature working:** the account is named after the real
person (BVN-verified), not the platform. Amounts paid into `9845221370` are that
department's money.

> **Important caveat (same day, later):** that account was issued once, at ~18:09
> UTC, and **new users have not received one since.** Every user created after it
> (either persona BVN, with the KYC profile before *or* after `start-nigeria`)
> came back with `nigerianAccounts: []` and only the pooled `activationAccounts`.
> The original account still reads back fine. So dedicated VBAs exist here — and
> this backend is built for them — but the sandbox is currently not issuing new
> ones. See §7 for the evidence and the exact question for BMONI.

> **Gotcha that will bite us:** `activationAccounts` (`accountName: "Bkey
> Limited"`, `targetCurrency: "EUR"`) is a *pooled* provider account and is
> **not** the department's account. Read `nigerianAccounts` (and match
> `targetCurrency: "NGN"`) when showing a department's VBA on the dashboard.
> Showing `activationAccounts` would put "Bkey Limited" on a student's payment
> screen.

### What still needs the docs' own reading
The `start-nigeria` body needs `bvn`, `ngnWalletAddress`, `ngnWalletIndex`. The
sandbox **accepted a well-formed placeholder address** (`0xabcdef…`) and still
issued the account, so account issuance is testable without a wallet — but treat
that as sandbox leniency, not production behaviour.

---

## 2. What the shared key cannot do

| Blocked | Why |
|---|---|
| **Register our own webhook** | `POST /v1/webhooks/config` is one-per-partner. The shared key's partner already has one, pointing at **someone else's** demo (`https://bmoni-hackathon-demo.workers.dev/webhooks/bmoni`). A second POST returns `409`; `PATCH` would hijack another team's deliveries. **Do not call either.** |
| **Receive real deliveries** | Same reason — the subscription points elsewhere. |
| **Money actually landing** | Sandbox wallets start empty; test tokens must be requested by email (`developers@bkey.me`). |
| **Wallet creation** | Needs an ECDSA owner-proof signature (`POST …/owner-proof-challenges` → sign → `POST …/smart-wallets/create-managed`). Server-side is possible with a generated keypair (`eth-account`/`web3` — **not currently in `backend/requirements.txt`**), or zero-signing via the invite flow where the BMONI app creates the wallet. |
| **Production** | The shared key is published in the docs and sandbox-only. |

**Bottom line:** the shared key is enough to build and test the funnels we need
(user creation, KYC, NGN account issuance, all reads, error/retry handling), but
not the webhook-driven deposit crediting. For that we need our own partner key.

---

## 3. Copy-paste walkthrough (verified)

PowerShell, from anywhere (no Django needed yet). Every call below returned the
status in the comment.

```powershell
$key  = 'pk_a025cacbf33a_76fb864113f3540909de5b1da39cc146906e35b1c6d4d1e4'  # public sandbox key
$base = 'https://embedded-dev.bmoni.com'
$h    = @{ 'x-api-key' = $key }

# 0. Sanity: key present -> 200; key omitted -> 401
Invoke-RestMethod "$base/v1/health"
Invoke-RestMethod "$base/v1/users" -Headers $h

# 1. Create the department account holder -> 201, keep bmoniUserId
#    Persona details must match exactly, or identity verification fails on purpose.
#    Sandbox personas: Bunch Dillon (BVN 95888168924) / Samson Jabo (BVN 22222222222)
$suffix = Get-Random -Minimum 100000 -Maximum 999999
$user = Invoke-RestMethod "$base/v1/users" -Method Post -Headers $h -ContentType 'application/json' -Body (@{
    firstName   = 'Bunch'
    lastName    = 'Dillon'
    email       = "dpt.persona.$suffix@example.com"
    phoneNumber = "+234801$suffix"
    bvn         = '95888168924'
} | ConvertTo-Json)
$uid = $user.user.bmoniUserId           # <-- persist this; the key to everything after

# 2. KYC profile (BVN auto-fills a lot; state must be a real Nigerian state)
Invoke-RestMethod "$base/v1/users/$uid/kyc" -Method Patch -Headers $h -ContentType 'application/json' -Body (@{
    personalInfo = @{ firstName = 'Bunch'; lastName = 'Dillon'; dateOfBirth = '1990-01-15'; gender = 'male' }
    address      = @{ streetLine1 = '15 Admiralty Way'; city = 'Lagos'; state = 'Lagos'; postalCode = '101241'; countryCode = 'NGA' }
} | ConvertTo-Json -Depth 5)

# 3. Start Nigeria onboarding -> 200; THIS is what issues the NGN virtual account
Invoke-RestMethod "$base/v1/users/$uid/onboarding/start-nigeria" -Method Post -Headers $h -ContentType 'application/json' -Body (@{
    bvn              = '95888168924'
    ngnWalletAddress = '0xabcdef1234567890abcdef1234567890abcdef12'
    ngnWalletIndex   = 1
} | ConvertTo-Json)

# 4. Read the department's account number to show on the dashboard -> 200
$accounts = Invoke-RestMethod "$base/v1/users/$uid/bank-accounts/deposit-accounts" -Headers $h
$accounts.nigerianAccounts              # accountName / bankName / accountNumber (+ bankCode)
```

Node/curl equivalent for any single step:

```bash
curl -s "https://embedded-dev.bmoni.com/v1/users" \
  -H "x-api-key: pk_a025cacbf33a_76fb864113f3540909de5b1da39cc146906e35b1c6d4d1e4"
```

### Test users already created in the sandbox (reuse them, no need to re-create)

| Name | `bmoniUserId` | Notes |
|---|---|---|
| Team Visionary | `f2989b43-6185-4b66-99c2-7250f35283ce` | No BVN — the "incomplete user" case |
| Bunch Dillon (persona) | `ec6c5a47-70af-43f5-9559-b5230b8749d9` | Has NGN VBA `9845221370` (PROVIDUS BANK) |

---

## 4. Gotchas worth money (learned by hitting them)

| Gotcha | Detail |
|---|---|
| **`activationAccounts` ≠ the department account** | Pooled provider account (`Bkey Limited`, `targetCurrency: EUR`). Read `nigerianAccounts`. |
| **Duplicate user create is `409`, not an error** | There are no idempotency keys. A `409` means a previous attempt landed → recover the existing person instead of retrying. |
| **Email AND phone are unique; the BVN is not** | `409 {"message":"User already exists with this email"}`; reusing a persona BVN is fine (201). So a recovery lookup must match **either** detail — the 409 never says which one collided. |
| **`GET /v1/users` is paginated** | `{users, total, page, limit}`, default `limit` 20 — the shared partner already holds **886** people. A lookup that only reads page 1 silently misses the person, which turns "recover, don't duplicate" into a dead end. Use `?page=&limit=` (the client pages up to 1,000). |
| **A `409` is not proof it is *our* person** | Two departments nominating the same email or phone would share one bank account, and nobody would notice until the money landed. This backend refuses that (`409 conflict`) instead of attaching both. |
| **Never blind-retry wallet creation** | No uniqueness guard exists; a retry can fork a second wallet. Read `GET …/smart-wallets/account/wallets` first. |
| **Webhook header is `x-webhook-signature`** | HMAC-**SHA256** over the **raw** request body (verify before parsing JSON), keyed with the subscription's `secretKey` (64-char hex). `x-webhook-event-id` carries a stable event id — use it for dedupe. Our Paystack handler is SHA512 + `x-paystack-signature`, so the BMONI view needs its own check. |
| **Subscribe to `employee.*`, not `wallet.*`** | Partner-scoped subscriptions rename money events (`employee.deposit.completed`, `employee.withdrawal.completed`). A `wallet.deposit.completed` subscription delivers nothing. |
| **`partnerId` is deprecated in the webhook config** | The spec says the config is always scoped to the calling API key's partner, so omit the field. (The prose docs still describe the old "legacy global subscription" behaviour — the spec wins, but confirm with BMONI.) |
| **Amounts are decimal strings** | `"100.00"`, never a number. Parse with `Decimal`, exactly like the Paystack paths (`directives/python_skill.md`). |
| **No stable machine-readable error codes** | Errors are `{statusCode, message, error}` and `message` can be a **string or an array**. Branch on `statusCode`; log `message`, never parse it. |
| **Rate limits** | Seen on responses: `x-ratelimit-limit-short: 5`, `-medium: 30`, `-long: 100`. Batch and back off. |
| **`x-api-key`, not `Authorization`** | The partner key goes in `x-api-key`, not a bearer token. |
| **Do not commit the partner `secretKey`** | `GET /v1/webhooks/config` returns it in plain text. It belongs in the secret manager / `.env`, never in a committed test fixture — generate a random secret locally for unit tests. |

---

## 5. What we need from BMONI (and why)

Email `developers@bkey.me` (there is no self-serve partner signup) for:

1. **Our own sandbox partner API key** — so the webhook subscription is *ours*
   (routes to our URL, verifies with our secret) and the employees we invite
   belong to our partner, not the shared "BMONI Hackathon" one.
2. **Sandbox test tokens** — so a deposit actually lands and produces
   `employee.deposit.completed`.
3. **Confirmation of two things the docs leave open:** whether production issues
   a **dedicated** VBA per account holder like the sandbox did (versus a pooled
   account you attribute by reference), and which field of a deposit event is
   authoritative for reconciliation.
4. **Why new dedicated VBAs stopped being issued in the sandbox** — see §7. Is it
   KYC activation, a per-partner allowance, or a temporary sandbox problem? It
   decides whether the demo can show a real per-department account or only the
   completed plumbing around it.

No partner ID is required any more (deprecated), so nothing else blocks us.

---

## 6. What is built — Phase 1 (shipped 2026-09-26)

Everything below is in the repo and tested; the sandbox walkthrough in §3 is what
it automates.

| Piece | Where |
|---|---|
| Settings (`BMONI_API_KEY`, `BMONI_BASE_URL`, `BMONI_WEBHOOK_SECRET`) | `backend/core/settings.py` — the key is **optional**, so the app boots without it and the endpoints answer `503` instead of inventing an account |
| API client + provisioning flow | `backend/apps/payments/bmoni_client.py` — `timeout=10`, `RequestException`/5xx → `BMONIUnavailable` → `502`, 4xx → `BMONIError`, `409` treated as *already created* (recover, never retry), `activationAccounts` never selected |
| Models + migration | `DepartmentBMONIWallet` / `BMONIWebhookEvent` in `backend/apps/payments/models.py`, migration `0006_…` (a DB CHECK refuses an `active` wallet with no account number) |
| Endpoints | `GET`/`POST` `/api/payments/departments/{id}/bank-account/`, `POST` `/api/payments/bmoni/webhook/` — documented in `docs/API_CONTRACT.md` §4a |
| Admin | Both models registered (account fields read-only; webhook events are immutable proof rows) |
| Tests | `backend/apps/payments/tests_bmoni.py` — 36 hermetic tests (faked HTTP, so the real client runs); `tests_bmoni_live.py` — opt-in real sandbox checks: `$env:BMONI_LIVE_TESTS='1'; python manage.py test apps.payments.tests_bmoni_live` |

Deliberate limits of Phase 1: the BVN is never stored (last four digits only), and
a deposit callback is **archived, not credited** — `processed=False` rows in
`BMONIWebhookEvent` are the Phase 2 worklist.

**Still blocked** until our own key and sandbox test tokens arrive: our own webhook
subscription (so real deliveries reach us) and end-to-end deposit crediting.

---

## 7. Open blocker: the sandbox is not issuing new dedicated VBAs

**Symptom.** A brand-new user — persona BVN, fresh email and phone — runs
`POST /v1/users` → `PATCH …/kyc` → `POST …/onboarding/start-nigeria` →
`GET …/bank-accounts/deposit-accounts` and gets:

```json
{ "nigerianAccounts": [],
  "activationAccounts": [{ "accountName": "Bkey Limited",
                           "accountNumber": "6177463833",
                           "targetCurrency": "EUR" }] }
```

**What we ruled out** (all tested 2026-09-26, each with a fresh user):

| Suspect | Result |
|---|---|
| Wrong BVN / wrong persona name | Same outcome for `95888168924` (Bunch Dillon) and `22222222222` (Samson Jabo) |
| The BVN had already been used | Reusing a BVN returns `201` — it is not a uniqueness rule |
| KYC profile missing, or sent too late | `PATCH …/kyc` **before** and **after** `start-nigeria` → same empty list |
| Our client, or our parsing | The user that *did* get an account still reads `9845221370` (PROVIDUS BANK, named after the holder) through the very same code path |
| The wallet address we send | A placeholder was accepted both at the successful issuance and now |

**Clue in the KYC response.** `PATCH …/kyc` returns `canActivate: false` with
`missing: ["proofOfAddressDocuments", "sourceOfFunds",
"identificationDocuments", "biometricDocuments"]` — the profile is `in_progress`,
not activated. The one successful issuance happened before we looked at this, so
we cannot yet tell whether issuance needs activation, a partner allowance, or a
sandbox quota that has since been used up.

**How the backend behaves meanwhile — deliberately:** `POST
/payments/departments/{id}/bank-account/` answers **`502 gateway_unavailable`**,
records the failure on the department's row, and stores **no** account. It never
falls back to the pooled `activationAccounts` bucket: contributions must not land
in an account we cannot put a department's name on.

**Question for BMONI** (added to the email in §5): in the sandbox, is dedicated
VBA issuance gated on KYC activation (those four document types), limited per
partner, or currently degraded? And in production, is a dedicated VBA per
onboarded person the expected result of this call sequence?

---

## Related

- `API_CONTRACT.md` — the existing contract the new read endpoint joins.
- `FRONTEND_LINKING.md` — where a department bank-account card would be wired.
- `../directives/python_skill.md` — Decimal and webhook-verification rules, which
  apply here unchanged.



