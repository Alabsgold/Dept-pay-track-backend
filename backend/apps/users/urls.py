from django.urls import path
from .views import (
    RegisterView,
    LoginView,
    LogoutView,
    CurrentUserView,
    DepartmentListView,
    ImportStudentsView,
    ClaimAccountView,
    ClaimBatchListView,
    DeactivateClaimBatchView,
    IssueResetCodeView,
    ResetPasswordView,
    SetUserRoleView,
)

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/login/', LoginView.as_view(), name='auth-login'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/me/', CurrentUserView.as_view(), name='auth-me'),
    path('departments/', DepartmentListView.as_view(), name='departments-list'),

    # Bulk roster import + account claiming (imported students have no
    # password until they claim with matric + first name + batch code).
    path('auth/import/', ImportStudentsView.as_view(), name='auth-import'),
    path('auth/claim/', ClaimAccountView.as_view(), name='auth-claim'),
    path('auth/claim-batches/', ClaimBatchListView.as_view(), name='claim-batches'),
    path(
        'auth/claim-batches/<int:pk>/deactivate/',
        DeactivateClaimBatchView.as_view(),
        name='claim-batch-deactivate',
    ),

    # Assisted password reset (Option A): rep/admin issues a one-time code;
    # the student sets the new password themselves.
    path('auth/reset-code/', IssueResetCodeView.as_view(), name='auth-reset-code'),
    path('auth/reset-password/', ResetPasswordView.as_view(), name='auth-reset-password'),

    # Admin-only promotion: reps are normal students until ticked.
    path('auth/users/<int:pk>/set-role/', SetUserRoleView.as_view(), name='auth-set-role'),
]
