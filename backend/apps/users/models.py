from django.contrib.auth.models import AbstractUser
from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    faculty = models.CharField(max_length=150)

    class Meta:
        ordering = ['name']
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'

    def __str__(self):
        return f"{self.name} ({self.faculty})"


class User(AbstractUser):
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

    email = models.EmailField(unique=True)
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
