from rest_framework.permissions import BasePermission
from .models import User


class IsStudent(BasePermission):
    """
    Allows access only to authenticated students.
    """
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == User.ROLE_STUDENT
        )


class IsClassRep(BasePermission):
    """
    Allows access only to authenticated class reps.
    """
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == User.ROLE_CLASS_REP
        )


class IsAdminUser(BasePermission):
    """
    Allows access only to admin users or staff.
    """
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role == User.ROLE_ADMIN or request.user.is_staff or request.user.is_superuser)
        )


class IsClassRepOrAdmin(BasePermission):
    """
    Allows access to class representatives or admins (including staff/superusers).
    """
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (
                request.user.role in [User.ROLE_CLASS_REP, User.ROLE_ADMIN]
                or request.user.is_staff
                or request.user.is_superuser
            )
        )
