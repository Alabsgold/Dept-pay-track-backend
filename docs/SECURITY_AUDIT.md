# Security Audit & Hardening Notes — Backend
**Date:** Sept 7 - 8, 2026 | **By:** Emmanuel (Backend)
**Status:** Done right after finishing the Users/Auth module before starting Contributions & Payments

---

### Why I did this audit
After getting the auth stuff running and passing initial tests, I felt like I should double-check our security before we build payments and contributions on top of it. Since this is dealing with real student dues and Paystack, if someone hacks or abuses our endpoints during the NACOS demo or in production we'd look really bad.

So I took a couple hours to go through our settings, views, and serializers looking for dumb mistakes or vulnerabilities. Honestly pretty glad I did because I caught a few really nasty issues that would have bitten us hard later.

---

### What I found & what I fixed

#### 1. Critical & High Stuff (Fixed all of these immediately)
- **Hardcoded SECRET_KEY fallback (C-1):** In `settings.py`, there was a fallback string if `SECRET_KEY` wasn't in `.env`. If someone deployed without setting the env var, that known key would be used, meaning anyone could forge sessions or tamper with tokens. I stripped out the fallback completely — now if `SECRET_KEY` is missing, Django will crash loudly on startup so we notice right away.
- **DEBUG defaulting to True (H-1):** I had `DEBUG` default to `True` in settings which was lazy of me. If we deployed to Render and forgot the env var, a 500 error would dump our entire database settings and secret keys right onto the screen. Changed it so `DEBUG` defaults to `False` unless explicitly set in `.env`.
- **Weak password acceptance (H-2):** Our registration serializer was literally only checking `min_length=6` and wasn't running Django's built-in password validators! Someone could register with `123456` or `password` on a payment portal. I hooked up `validate_password()` directly and bumped the minimum length to 8 characters. Tested it and weak passwords get rejected properly now.
- **No rate limiting / brute force risk (H-3):** Anyone could write a simple bash loop and spam `/api/auth/login/` or `/api/auth/register/` thousands of times per minute. I added DRF's `ScopedRateThrottle` with an `auth` scope set to `10/min` per IP. It’s plenty for normal users but stops someone trying to brute-force student passwords.
- **Account enumeration (H-4):** The registration endpoint was returning "A user with this email already exists" vs "A user with this matric number already exists". An attacker could probe emails or matric numbers to see who's registered. I changed it to generic messages so we don't leak student lists. Email verification is ideal but for a 16-day hackathon that's overkill so generic errors will do for now.

#### 2. Medium & Annoying Bugs (Fixed & Deferred)
- **Case-sensitive lookups (M-1):** If someone registered `JDOE@school.edu.ng` and later `jdoe@school.edu.ng`, both would pass because standard lookups were case-sensitive. Swapped the duplicate queries to use `__iexact`. Fixed.
- **Production HTTPS / Security Headers (M-2):** We don't have `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, or HSTS turned on yet because we are testing locally over plain HTTP. Definitely need to flip these on right before we push to Render. Logged it in `BOTTLENECKS.md` so I don't forget.
- **Tokens don't expire (M-3):** Right now DRF auth tokens don't expire unless the user logs out. We agreed in the brief this is fine for the hackathon demo, but once HTTPS is enforced it's reasonably secure anyway.
- **Database URL & Postgres (M-4):** We have `DATABASE_URL` in `.env.example` but local settings use SQLite. Render's filesystem is ephemeral so if the server restarts on Render, SQLite wipes! I deferred the Postgres wiring until our deployment prep milestone.
- **No logging / audit trail (M-5):** Need proper logging when webhooks hit or payments fail. Will add this when I build the payments app next.

#### 3. Low / Hygiene Stuff
- Pinned all dependencies in `requirements.txt` to exact versions (`Django==5.2.17`, `djangorestframework==3.18.1`, `python-decouple==3.8`, `requests==2.34.2`) so nothing randomly breaks if a package updates overnight.

---

### Ground rules I added for Payments & Contributions (API_CONTRACT §8)
Before I let the next sub-agents or myself write payments/contributions code, I wrote these strict rules into `API_CONTRACT.md` so we don't introduce vulnerabilities:
1. **Always verify webhook amount:** Paystack sends amounts in kobo (e.g. 500000 for 5000 Naira). We must divide by 100 and assert it matches our DB `Payment.amount` exactly. If someone modifies the client payload and pays 1 Naira instead of 5000, we must flag it as `failed`, never `success`!
2. **Webhook idempotency:** Payment webhooks can arrive twice from Paystack. The webhook handler has to be idempotent — if a payment is already marked `success`, receiving another webhook should just return 200 without creating duplicate transactions or double-crediting dues.
3. **No IDOR (Object-level permission checks):** A student must only be able to view their own payment history, receipts, and notifications. Nobody should be able to view someone else's receipt by just changing the URL ID. And class reps should only see payments for their own department.
4. **Analytics restricted:** Only class reps and admins can view collection stats and outstanding student lists.

---

### Verification
I updated the test suite in `apps/users/tests.py` with tests for the new password rules and throttling.
Ran the full test suite:
`python manage.py test apps.users`
All 10 tests passed cleanly without any issues:
```text
Creating test database for alias 'default'...
..........
----------------------------------------------------------------------
Ran 10 tests in 30.115s

OK
```

Everything is solid and locked down. Ready to move onto Contributions and Payments now.