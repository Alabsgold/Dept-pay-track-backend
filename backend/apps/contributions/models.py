from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.users.models import Department, User

from . import payments_bridge


class Contribution(models.Model):
    """
    A fee/dues a department asks its students to pay.

    Money rule (BRIEF.md / skills.md): `amount` is always a DecimalField and
    must never be touched by a student. The amount is fixed by the class
    rep/admin who creates the contribution; payment flows (future payments
    app) must read THIS amount, never take one from the client.
    """

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name='contributions',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='contributions_created',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    deadline = models.DateTimeField(null=True, blank=True)
    is_mandatory = models.BooleanField(default=True)
    target_level = models.CharField(
        max_length=10,
        choices=User.LEVEL_CHOICES,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['department', 'created_at']),
            models.Index(fields=['department', 'deadline']),
            models.Index(fields=['department', 'target_level']),
        ]
        verbose_name = 'Contribution'
        verbose_name_plural = 'Contributions'

    def __str__(self):
        return self.title

    @property
    def is_active(self):
        """A contribution students can still see/pay: no deadline or deadline ahead."""
        return self.deadline is None or self.deadline >= timezone.now()

    def eligible_students(self):
        """Users who owe/see this contribution (department + level + role).

        Class reps ARE students and still pay dues; only admins/staff are
        exempt (owner decision, 2026-09-16).
        """
        qs = User.objects.filter(
            department=self.department,
            role__in=[User.ROLE_STUDENT, User.ROLE_CLASS_REP],
        )
        if self.target_level:
            qs = qs.filter(level=self.target_level)
        return qs

    # --- Payment-aware helpers (see payments_bridge docstring) ------------

    def has_paid(self, user):
        return payments_bridge.has_paid(self, user)

    def total_expected(self):
        return self.amount * self.eligible_students().count()

    def total_collected(self):
        return payments_bridge.total_collected(self)

    def outstanding_count(self):
        return max(
            self.eligible_students().count() - len(payments_bridge.paid_student_ids(self)),
            0,
        )