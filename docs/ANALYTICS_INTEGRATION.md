# Analytics Integration — handover for the Data/AI teammate

**Who this is for:** the teammate building `GET /api/analytics/*` (§6 of
`docs/API_CONTRACT.md`).

**Why it isn't built by the backend agent:** §6 is listed in the contract as a
separate deliverable. The backend agent deliberately **did not** build it — that
would be taking over another teammate's scope. Instead, everything needed to
build it cleanly is documented below.

**Golden rule:** consume the backend through the API and the sanctioned helpers.
**Do not edit anything under `backend/apps/`**, and do not open a direct database
connection. If you need a value the API doesn't expose, ask for it — it will be
added to the contract first.

---

## 1. The data source you should use

These already exist and are the **only** sanctioned way to read money figures.
Using them guarantees your numbers match the rest of the system (they are the
same functions the roster and summary endpoints use).

### `apps.contributions.models.Contribution`

| Method | Returns | Meaning |
|---|---|---|
| `total_expected()` | `Decimal` | Fee amount × number of eligible students |
| `total_collected()` | `Decimal` | Sum of **successful** payments for this fee |
| `outstanding_count()` | `int` | Eligible students who have **not** paid |
| `eligible_students()` | `QuerySet[User]` | Who owes this fee (department + `target_level`, role `student` or `class_rep`) |
| `has_paid(user)` | `bool` | Whether one student has a successful payment |
| `is_active()` | `bool` | Fee deadline has not passed |

### `apps.contributions.payments_bridge`

Read helpers safe for reuse: `payments_for(contribution)`,
`has_paid(contribution, user)`, `paid_student_ids(contribution)`,
`total_collected(contribution)`, `payment_status_map(contribution)`,
`already_paid(contribution, student)`.

> **Note for the model layer:** `eligible_students()` deliberately **excludes
> admins/staff** — faculty are exempt from dues — but **includes class reps**,
> because a rep is still a student and still owes. `total_expected()` and
> `outstanding_count()` both inherit this rule, so your figures will match the
> roster the department sees.

If you only need a single fee's three headline numbers, `GET
/api/contributions/{id}/summary/` already returns them — reuse it rather than
recomputing.

---

## 2. Permissions (§8 rules)

| Rule | Detail |
|---|---|
| Who may read analytics | Reps and admins only — use `apps.users.permissions.IsClassRepOrAdmin` |
| Students must never see cross-student aggregates | Excluding this leaks who has and hasn't paid |
| Recommend a **service account** | A dedicated user with role `class_rep`, so your dashboard's access is revocable and identifiable in logs |
| Standard error shape | Always `{"error": "<code>", "message": "<human text>"}` via `core.exceptions.custom_exception_handler` |
| Unauthenticated requests | Must return `401` |

---

## 3. Expected response shapes (§6)

**`GET /analytics/collection-stats/`** — money as **strings**, never floats.

```json
// 200
{
  "total_expected": "175000.00",
  "total_collected": "122500.00",
  "outstanding_count": 15
}
```

**`GET /analytics/outstanding-students/?contribution_id=5`**

```json
// 200
[
  { "student": "Ada Obi", "matric_number": "CSC/2026/001", "level": "400" }
]
```

**Hard requirements**

- Use `DecimalField` / `Decimal` for all money. **Never `float`** — it is the one
  class of bug we treat as unacceptable in this codebase.
- Serialize money as a **2-decimal string** (`"3500.00"`), not a number.
- Return `400` for a missing or non-numeric `contribution_id`.
- Return the standard `{"error", "message"}` shape for every failure.

---

## 4. Sandboxing rules (both directions)

| Rule | Reason |
|---|---|
| Analytics code lives in **your own app** (e.g. `backend/apps/analytics/`) | One owner per app keeps blame and review clean |
| Never edit `users/`, `contributions/`, `payments/`, `notifications/` source | Those are covered by 149 tests; a silent edit can break settlement rules |
| If you need new data or a new field, **request a contract change first** | `docs/API_CONTRACT.md` is the single source of truth; code and tests move with it |
| Read-only access to the money tables | Analytics must never write payments, statuses or refund flags |
| Do not bypass the bridge/helpers with raw ORM aggregates | Otherwise your totals will drift from the roster's totals |

**Mutual protection:** the contract tests (`apps/*/tests_contract.py`) are the
guardrail. If your change breaks one, that is the system telling you a documented
shape moved — reconcile the docs and tests together, never patch around it.

---

## 5. Verifying your work

```bash
cd backend
python manage.py test                     # 149 tests must stay green
python manage.py check                    # system check
```

Add your own tests alongside your app so your numbers are locked too. A useful
first test: for a fee where every eligible student has paid,
`total_expected() == total_collected()` and `outstanding_count() == 0`.

---

## 6. Open question for the owner

Whether §6 should aggregate **across** departments (a faculty-wide view) or stay
per-department is not yet decided. Per-department is the safe default, since
`eligible_students()` and the roster are department-scoped. Confirm before
building the cross-department variant.
