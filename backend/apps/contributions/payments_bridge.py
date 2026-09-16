"""
Read/write bridge between the contributions app and the payments app.

The payments app is owned by a teammate (``apps/payments``). Contributions
needs to READ payment data for ``has_paid``, collection totals, and per-student
status, and it needs ONE write: marking a student as paid offline.

To do that without breaking the teammate's branch:

  * We only add fields to the ``Payment`` model via a NEW migration in the
    payments app (additive: contribution FK + method + recorded_by). Her
    existing code keeps working untouched.
  * The bridge introspects the app registry at call time. If "our" fields
    aren't there yet, it returns safe defaults (nothing collected, nobody paid)
    and the manual-mark helper raises a clear error instead of guessing.
"""

from decimal import Decimal

from django.apps import apps as django_apps

# Matches apps.payments.models.Payment statuses.
STATUS_PENDING = 'pending'
STATUS_SUCCESS = 'success'

# Payment method: online via Paystack, or manually recorded by a rep/admin.
METHOD_ONLINE = 'online'
METHOD_MANUAL = 'manual'


def payment_model():
    """Return the Payment model if safely available, else None."""
    if not django_apps.is_installed('apps.payments'):
        return None
    for label in ('payments', 'apps.payments'):
        try:
            return django_apps.get_model(label, 'Payment', require_ready=False)
        except LookupError:
            continue
    return None


def _has_field(pm, name):
    return any(f.name == name for f in pm._meta.get_fields())


def has_contribution_link(pm=None):
    """True only when Payment has both `contribution` + `method` (our fields)."""
    pm = pm or payment_model()
    if pm is None:
        return False
    return _has_field(pm, 'contribution') and _has_field(pm, 'method')


def payments_for(contribution):
    """Queryset of this contribution's payment rows, or None if unavailable."""
    pm = payment_model()
    if pm is None or not has_contribution_link(pm):
        return None
    return pm.objects.filter(contribution=contribution)


def has_paid(contribution, user):
    if user is None:
        return False
    qs = payments_for(contribution)
    if qs is None:
        return False
    return qs.filter(student=user, status=STATUS_SUCCESS).exists()


def paid_student_ids(contribution):
    qs = payments_for(contribution)
    if qs is None:
        return set()
    return set(qs.filter(status=STATUS_SUCCESS).values_list('student_id', flat=True))


def total_collected(contribution):
    from django.db.models import Sum

    qs = payments_for(contribution)
    if qs is None:
        return Decimal('0.00')
    total = qs.filter(status=STATUS_SUCCESS).aggregate(total=Sum('amount'))['total']
    return total if total is not None else Decimal('0.00')


def payment_status_map(contribution):
    """
    {student_id: {'status': ..., 'paid_at': ...}} for this contribution.

    ``paid_at`` maps to the payment row's ``updated_at`` once a status of
    ``success`` is reached. Returns {} when the payments app isn't linked yet,
    so callers treat every eligible student as unpaid.
    """
    qs = payments_for(contribution)
    if qs is None:
        return {}
    rows = qs.values('student_id', 'status', 'updated_at')

    # A student can have several attempts (a fresh attempt is allowed after a
    # failed/abandoned one), so collapse them deliberately instead of letting
    # row order decide: a `success` anywhere always wins, otherwise the newest
    # attempt's status is shown. `paid_at` is that success row's timestamp.
    # (Payment Meta.ordering is newest-first, so iteration is newest -> oldest.)
    status_map = {}
    for row in rows:
        student_id = row['student_id']
        entry = {
            'status': row['status'],
            'paid_at': (
                row['updated_at'] if row['status'] == STATUS_SUCCESS else None
            ),
        }
        existing = status_map.get(student_id)
        if existing is None or (
            existing['status'] != STATUS_SUCCESS
            and entry['status'] == STATUS_SUCCESS
        ):
            status_map[student_id] = entry
    return status_map


def already_paid(contribution, student):
    """True if there is already a successful payment banked for this student."""
    qs = payments_for(contribution)
    if qs is None:
        return False
    return qs.filter(student=student, status=STATUS_SUCCESS).exists()


def mark_manually_paid(contribution, student, recorded_by, receipt_reference=''):
    """
    Bank a successful manual payment (offline cash/transfer) for a student.

    The amount ALWAYS comes from the contribution (server-side), never from the
    client. `receipt_reference` (teller/receipt number quoted by the rep) is
    the audit hook that makes manual marks reconcilable. Returns the created
    Payment. Raises ValueError if the payments
    isn't linked yet (call it only once our additive migration is applied).
    """
    pm = payment_model()
    if pm is None or not has_contribution_link(pm):
        raise ValueError(
            "payments app not yet linked for manual marking "
            "(contribution FK/method migration not applied)"
        )

    created = pm.objects.create(
        student=student,
        contribution=contribution,
        payment_type='contribution',  # matches Payment.PAYMENT_CONTRIBUTION (bridge avoids importing the payments model)
        amount=contribution.amount,
        reference=(
            f"MANUAL-{contribution.id}-{student.id}"
        ),
        status=STATUS_SUCCESS,
        method=METHOD_MANUAL,
        recorded_by=recorded_by,
        receipt_reference=receipt_reference,
    )
    return created