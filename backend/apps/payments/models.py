from django.conf import settings
from django.db import models


class Payment(models.Model):
    PAYMENT_DEPARTMENTAL_FEE = 'departmental_fee'
    PAYMENT_SPORTS_JERSEY = 'sports_jersey'
    PAYMENT_EXCURSION = 'excursion'

    PAYMENT_TYPE_CHOICES = [
        (PAYMENT_DEPARTMENTAL_FEE, 'Departmental Fee'),
        (PAYMENT_SPORTS_JERSEY, 'Sports Jersey'),
        (PAYMENT_EXCURSION, 'Excursion'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payments'
    )

    payment_type = models.CharField(
        max_length=30,
        choices=PAYMENT_TYPE_CHOICES
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=100,
        unique=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.student} - {self.payment_type} - {self.status}"