"""
Receiving-side signal handlers — the ONLY cross-app coordination point for
the notifications app (AGENTS.md Agent 4: never modify users/contributions/
payments source files; coordinate via Django signals only).

Payment alerts fire on *status transitions*:
  * A pre_save snapshot records the row's status before Django writes it.
  * post_save compares it: only a real transition (or a brand-new row banked
    directly as terminal — the manual mark-paid path) notifies. Webhook
    retries, the webhook's idempotent early-return and verify re-checks of an
    already-terminal row are naturally silent — one alert per transition.
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.contributions.models import Contribution
from apps.payments.models import Payment

from .models import Notification


def _truncate(message):
    """The message column is max_length=255; contribution titles reach 200."""
    return message[:255]


@receiver(pre_save, sender=Payment)
def snapshot_payment_status(sender, instance, **kwargs):
    """
    Remember the row's DB status BEFORE the write so post_save can tell a real
    status transition apart from an idempotent re-save. We deliberately do
    NOT touch the payments app itself — this stays a receiving-side hook.
    """
    if instance.pk:
        instance._notif_prev_status = (
            Payment.objects.filter(pk=instance.pk)
            .values_list('status', flat=True)
            .first()
        )
    else:
        instance._notif_prev_status = None


@receiver(post_save, sender=Payment)
def notify_on_payment_status_change(sender, instance, created, **kwargs):
    # New row: any terminal status counts as a transition (this is the manual
    # mark-paid path — the bridge banks the payment directly as `success`).
    # Existing row: notify only when the status actually changed.
    prev_status = None if created else getattr(
        instance, '_notif_prev_status', None
    )
    if not created and instance.status == prev_status:
        return  # idempotent re-save, no transition — stay silent

    if instance.status == Payment.STATUS_SUCCESS:
        notification_type = Notification.TYPE_PAYMENT_SUCCESS
    elif instance.status == Payment.STATUS_FAILED:
        notification_type = Notification.TYPE_PAYMENT_FAILED
    else:
        return  # still pending — nothing to announce yet

    fee = (
        f' for "{instance.contribution.title}"'
        if instance.contribution is not None
        else ''
    )
    if notification_type == Notification.TYPE_PAYMENT_SUCCESS:
        message = f'Your payment of ₦{instance.amount:,.2f}{fee} was successful.'
    else:
        message = f'Your payment of ₦{instance.amount:,.2f}{fee} failed.'

    Notification.objects.create(
        recipient=instance.student,
        notification_type=notification_type,
        message=_truncate(message),
        contribution=instance.contribution,
    )


@receiver(post_save, sender=Contribution)
def notify_on_new_contribution(sender, instance, created, **kwargs):
    if not created:
        return

    level_note = (
        f' (Level {instance.target_level})' if instance.target_level else ''
    )
    message = _truncate(
        f'New contribution posted: "{instance.title}" — '
        f'₦{instance.amount:,.2f}{level_note}.'
    )

    # eligible_students() already scopes to department + target_level.
    # bulk_create = one INSERT for the whole cohort, never a per-student loop.
    Notification.objects.bulk_create(
        Notification(
            recipient=student,
            notification_type=Notification.TYPE_NEW_CONTRIBUTION,
            message=message,
            contribution=instance,
        )
        for student in instance.eligible_students()
    )
