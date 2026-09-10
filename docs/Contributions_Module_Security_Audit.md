# Security Audit — Contributions & Next Steps
**Date:** Sept 10, 2026
**Reference:** `security_audit_skill.md`
**Scope:** `contributions` app & `core` configuration

---

### Project Status & Agent Context
Based on the previous `SECURITY_AUDIT.md` (which stated "Verdict: ready for Agent 2") and the current state of the codebase where the `contributions` app is fully implemented while the `payments` app integration is pending on an unmerged branch, **we are currently operating as Agent 2 (or at the transition point to Agent 3)**.

---

### Security Audit Findings

I performed a systematic security review using the guidelines in `security_audit_skill.md`. 

#### 1. OWASP Top 10 & Common Vulnerabilities
- **Injection (Pass):** The codebase strictly relies on the Django ORM. Parameterized queries are used by default, and no raw SQL vulnerabilities exist in the `users` or `contributions` apps.
- **Broken Access Control (Pass):** 
  - **IDOR Prevention:** The `_visible_contributions` helper securely scopes queries so that class reps and students can only access data belonging to their assigned `department_id`. 
  - **Privilege Checking:** The `ContributionListCreateView` strictly requires `IsClassRepOrAdmin` for `POST` requests, preventing standard students from creating contribution demands.
  - **Forced Ownership:** In `perform_create`, `department=user.department` and `created_by=user` are enforced server-side. The client cannot spoof ownership or create contributions for other departments.
- **Insecure Design (Pass):** The `payments_bridge.py` safely checks if the `payments` app is installed and linked, preventing application crashes and securely failing closed (returning unpaid statuses) until the integration is merged.

#### 2. Authentication & Authorization
- **Credential Management (Pass):** Handled securely via Django's password validators in the `users` app. Tokens are rotated upon login to prevent leaked tokens from surviving a re-login.
- **Authorization Context (Medium - Functional Bug):** In `ContributionListCreateView.perform_create`, there is a strict check: `if not user.department_id: raise ValidationError(...)`. While secure, this means that a system-wide `admin` or `superuser` who isn't assigned to a specific department cannot create contributions via the API. They must use the Django admin portal. (Consider if this is intended).

#### 3. Input Validation & Data Handling
- **Sanitization (Low - XSS Risk):** The `description` field in the `Contribution` model is a `TextField`. If class reps embed malicious HTML scripts, it will be saved. Ensure that the frontend safely renders this field (e.g., escaping it instead of using `dangerouslySetInnerHTML`).
- **Data Integrity (Pass):** The `amount` field is appropriately cast to a `DecimalField` with a `MinValueValidator(0.01)`, avoiding floating-point precision errors and zero-amount exploits.

#### 4. Dependency Management & Deployment Readiness
- **Outdated/Missing Packages (Medium - CORS):** `django-cors-headers` is not installed or configured in `settings.py`. If the frontend is hosted on a separate domain (e.g., Vercel) while the backend is on Render, all API calls will be blocked by the browser. 
- **Production Headers (Medium):** As noted in the previous audit, `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, and `HSTS` are still absent. These must be enabled before the final deployment.

---

### Recommendations for Next Steps (Payments Integration)
As we integrate the `payments` app next, remember the rules established in the API contract:
1. **Never trust client amounts:** When initiating or verifying payments, read the expected amount directly from `Contribution.amount`, not from the client payload.
2. **Idempotency:** Ensure the webhook handler ignores duplicate Paystack events for already-successful payments.
3. **Kobo Conversion:** Enforce the strict `/ 100` division when comparing Paystack's kobo amounts to the database's Naira amounts.
