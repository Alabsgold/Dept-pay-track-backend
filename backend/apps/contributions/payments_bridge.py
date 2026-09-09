"""
Read-only bridge between the contributions app and the payments app.

The payments app is owned by a teammate and sits on an unmerged branch
(``apps/payments``). Contributions needs a *read* of payment data for
``has_paid``, collection totals, and per-student status, but must not:

  * depend on the payments app being installed (it may not be yet), or
  * modify anything in ``apps/payments`` (teammate's code), or
  * trust that app's client-supplied amounts.

This module therefore introspects the Django app registry at call time:

  * If ``apps.payments`` is installed AND its ``Payment`` model has a
    ``contribution`` FK, we read from it.
  * Otherwise we return safe defaults: nothing collected, nobody paid.

The payments branch currently has a ``Payment`` model WITHOUT a
``contribution`` field, so until that integration is done (planned ticket:
"add contribution FK to Payment"), every student correctly reports unpaid.
"""

from decimal import Decimal

from django.apps import apps as django_apps

# Matches apps.payments.models.Payment.STATUS_SUCCESS on the payments branch.
STATUS_SUCCESS = 'success'


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


def has_contribution_link(pm=None):
    """True only when Payment has a `contribution` FK (post-integration)."""
    pm = pm or payment_model()
    if pm is None:
        return False
    return any(f.name == 'contribution' for f in pm._meta.get_fields())


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
    ``success`` is reached (the payment row's last change is the settle time).
    Returns {} when the payments app isn't linked yet, so callers treat every
    eligible student as unpaid.
    """
    qs = payments_for(contribution)
    if qs is None:
        return {}
    rows = qs.values('student_id', 'status', 'updated_at')
    return {
        row['student_id']: {
            'status': row['status'],
            'paid_at': row['updated_at'] if row['status'] == STATUS_SUCCESS else None,
        }
        for row in rows
    }