from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.utils import timezone


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    faculty = models.CharField(max_length=150)

    class Meta:
        ordering = ['name']
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'

    def __str__(self):
        return f"{self.name} ({self.faculty})"


class UserManager(DjangoUserManager):
    """Keep a missing email as NULL instead of Django's default ``''``.

    ``BaseUserManager.normalize_email(None)`` returns ``''``, and ``email`` is
    unique — so the second student imported without an email would collide on
    that unique index and abort the whole roster import. NULL is distinct in a
    unique index, so a full class list can be imported with no emails at all.
    """

    # We have no data migrations calling create_user, so there is nothing for
    # Django to freeze into a migration file.
    use_in_migrations = False

    @classmethod
    def normalize_email(cls, email):
        if not email:
            return None
        return super().normalize_email(email)


class User(AbstractUser):
    objects = UserManager()

    ROLE_STUDENT = 'student'
    ROLE_CLASS_REP = 'class_rep'
    ROLE_ADMIN = 'admin'

    ROLE_CHOICES = [
        (ROLE_STUDENT, 'Student'),
        (ROLE_CLASS_REP, 'Class Rep'),
        (ROLE_ADMIN, 'Admin'),
    ]

    LEVEL_100 = '100'
    LEVEL_200 = '200'
    LEVEL_300 = '300'
    LEVEL_400 = '400'
    LEVEL_500 = '500'

    LEVEL_CHOICES = [
        (LEVEL_100, '100'),
        (LEVEL_200, '200'),
        (LEVEL_300, '300'),
        (LEVEL_400, '400'),
        (LEVEL_500, '500'),
    ]

    # Nullable so imported students (who may not have an email on file yet)
    # can exist without one; unique still enforced for non-null values.
    email = models.EmailField(unique=True, null=True, blank=True)
    matric_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students'
    )
    level = models.CharField(
        max_length=10,
        choices=LEVEL_CHOICES,
        null=True,
        blank=True
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_STUDENT
    )
    phone_number = models.CharField(max_length=20, blank=True, default='')

    class Meta:
        ordering = ['id']
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class ClaimBatch(models.Model):
    """
    One shared claim code per CSV import — the entire credential-distribution
    task for a cohort. Imported accounts have NO password and cannot be logged
    into until the student claims them (matric + first name + this code).
    """
    code = models.CharField(max_length=12, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='claim_batches',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} (active={self.is_active})"


class PasswordResetCode(models.Model):
    """
    Single-use, short-lived code a rep/admin hands to a student in person
    (Option A — no email dependency). The student resets their own password
    with it; the rep never sees or sets the new password.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reset_codes',
    )
    code = models.CharField(max_length=12, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reset_codes_issued',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    RESET_TTL_MINUTES = 30

    class Meta:
        ordering = ['-created_at']

    def is_expired(self):
        return (
            self.used_at is not None
            or self.created_at
            < timezone.now() - timedelta(minutes=self.RESET_TTL_MINUTES)
        )

    def __str__(self):
        return f"{self.user} - {self.code}"

