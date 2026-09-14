# Manual Testing Guide — run and verify the whole backend like a real user
**No frontend required.** Everything below works with PowerShell, a browser, and Paystack's test mode. After every change, run the automated suite first (`python manage.py test` → currently **106 tests, OK**) — this guide is for seeing it work *for real*.

---

## 1. One-time setup

```powershell
cd c:\Users\USER\Departmental-Payment-Tracking\backend
.\venv\Scripts\activate                 # or use .\venv\Scripts\python.exe directly
python manage.py migrate
python manage.py createsuperuser        # for the Django admin panel
python manage.py runserver              # server on http://localhost:8000
```

**`.env` values (backend/.env, never committed):**
```
SECRET_KEY=<any long random string for local dev>
DEBUG=True
PAYSTACK_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
PAYSTACK_PUBLIC_KEY=pk_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Get Paystack test keys:** sign up / log in at https://dashboard.paystack.com → bottom-left switch to **Test mode** → Settings → **API Keys** → copy the *Test Secret Key* and *Test Public Key*. Test-mode money is fake.

**Django admin:** open http://localhost:8000/admin/ → create a **Department** (e.g. name `Computer Science`, faculty `Physical Sciences`). Note its id (usually 1) — registration needs `department_id`.

---

## 2. The helper: `simulate_webhook.py`

Paystack can't reach `localhost`, so webhooks normally need ngrok (§4). The included simulator signs payloads with your own secret key, so you can hit the webhook endpoint directly:

```powershell
python simulate_webhook.py <reference>           # success, correct amount
python simulate_webhook.py <reference> 35000     # wrong amount → marked failed
python simulate_webhook.py <reference>           # run twice → idempotent no-op
```

Use ngrok (§4) when you want the *real* Paystack→you round trip.

---

## 3. Real-user walkthrough (happy path)

```powershell
# 3.1 Register student Ada  (expected 201: {id, username, role, department, level})
$ada = Invoke-RestMethod -Uri http://localhost:8000/api/auth/register/ -Method Post -ContentType "application/json" -Body (@{username="ada"; email="ada@school.edu.ng"; password="S7rong!Passw0rd"; matric_number="CSC/2026/001"; department_id=1; level="400"} | ConvertTo-Json)

# 3.2 Register student Bode (for roster/manual-mark testing)
$bode = Invoke-RestMethod -Uri http://localhost:8000/api/auth/register/ -Method Post -ContentType "application/json" -Body (@{username="bode"; email="bode@school.edu.ng"; password="S7rong!Passw0rd"; matric_number="CSC/2026/002"; department_id=1; level="400"} | ConvertTo-Json)

# 3.3 Register rep Chidi — role is auto-forced to student at registration;
#     promote him in Django admin (Users → chidi → role = Class Rep → Save)
$chidi = Invoke-RestMethod -Uri http://localhost:8000/api/auth/register/ -Method Post -ContentType "application/json" -Body (@{username="chidi"; email="chidi@school.edu.ng"; password="S7rong!Passw0rd"; matric_number="CSC/2026/003"; department_id=1; level="500"} | ConvertTo-Json)

# 3.4 Login — response: {token, user:{id, username, role}}
$tokA = (Invoke-RestMethod -Uri http://localhost:8000/api/auth/login/ -Method Post -ContentType "application/json" -Body (@{username="ada"; password="S7rong!Passw0rd"} | ConvertTo-Json)).token
$tokC = (Invoke-RestMethod -Uri http://localhost:8000/api/auth/login/ -Method Post -ContentType "application/json" -Body (@{username="chidi"; password="S7rong!Passw0rd"} | ConvertTo-Json)).token
$hA = @{ Authorization = "Token $tokA" }; $hC = @{ Authorization = "Token $tokC" }
```

**REP — create a fee (class rep/admin only):**
```powershell
$fee = Invoke-RestMethod -Uri http://localhost:8000/api/contributions/ -Method Post -Headers $hC -ContentType "application/json" -Body (@{title="Departmental Shirt 2026"; description="Official shirt"; amount="3500.00"; deadline="2026-12-31T23:59:00Z"; is_mandatory=$true; target_level=$null} | ConvertTo-Json)
$feeId = $fee.id
```

**STUDENT — list fees + start a payment (M-1 check: response has `reference` + `checkout_url`):**
```powershell
Invoke-RestMethod -Uri http://localhost:8000/api/contributions/ -Headers $hA          # has_paid: false
$pay = Invoke-RestMethod -Uri http://localhost:8000/api/payments/initiate/ -Method Post -Headers $hA -ContentType "application/json" -Body (@{contribution_id=$feeId} | ConvertTo-Json)
$pay.reference; $pay.checkout_url      # ← open checkout_url in your browser
```

**PAY on the checkout page with the Paystack test card:** `4084 0840 8408 4081`, any future expiry, CVC `408`, follow the test OTP/bank prompts → success. (Failure-path card: `4084 0840 8408 4082` = insufficient funds.)

**Webhook lands automatically** if ngrok is configured (§4) — the runserver console shows `POST /api/payments/webhook/ 200`. Otherwise simulate now:
```powershell
python simulate_webhook.py $pay.reference      # → 200 {'received': True}   (M-2 check)
```
