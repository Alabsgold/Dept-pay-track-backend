from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import User
from apps.users.permissions import IsClassRepOrAdmin

from . import payments_bridge
from .models import Contribution
from .serializers import ContributionSerializer


def _visible_contributions(user):
    """
    Contributions a user may see/own, scoped to their department.

    Students and class reps only ever reach their own department's rows;
    staff/superusers (no department today) get all rows. This is the single
    choke point that prevents cross-department IDOR on detail/summary/payments.
    """
    qs = Contribution.objects.select_related('department', 'created_by').all()
    if user.department_id:
        qs = qs.filter(department_id=user.department_id)
    elif not (user.is_staff or user.is_superuser):
        qs = qs.none()
    return qs


def _display_name(user):
    return ' '.join(p for p in (user.first_name, user.last_name) if p) or user.username


class ContributionListCreateView(generics.ListCreateAPIView):
    serializer_class = ContributionSerializer

    def get_permissions(self):
        # Only class reps/admins may create (API_CONTRACT.md section 3).
        if self.request.method == 'POST':
            return [IsClassRepOrAdmin()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = _visible_contributions(self.request.user)

        if self.request.method == 'GET' and self.request.user.role == User.ROLE_STUDENT:
            # Students only see contributions still open, and only their level.
            now = timezone.now()
            qs = qs.filter(Q(deadline__isnull=True) | Q(deadline__gte=now))
            if self.request.user.level:
                qs = qs.filter(
                    Q(target_level__isnull=True) | Q(target_level=self.request.user.level)
                )
            else:
                qs = qs.filter(target_level__isnull=True)

        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if not user.department_id:
            raise serializers.ValidationError(
                {
                    'non_field_errors': (
                        'You must belong to a department before you can create contributions.'
                    )
                }
            )
        # Owner + department are ALWAYS set server-side. The client can never
        # choose who created it or which department it belongs to.
        serializer.save(department=user.department, created_by=user)


class ContributionDetailView(generics.RetrieveAPIView):
    serializer_class = ContributionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return _visible_contributions(self.request.user)


class ContributionSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        contribution = get_object_or_404(_visible_contributions(request.user), pk=pk)
        return Response(
            {
                'total_expected': str(contribution.total_expected()),
                'total_collected': str(contribution.total_collected()),
                'outstanding_count': contribution.outstanding_count(),
            }
        )


class ContributionPaymentsView(APIView):
    """
    GET /contributions/{id}/payments/ — class rep/admin only.

    Lists every eligible student's payment status for this contribution.
    Until the payments app is integrated, the bridge reports no payments, so
    every eligible student shows status "pending" (the honest state of the
    data today — no payment rows exist to attribute to this contribution).
    """

    permission_classes = [IsClassRepOrAdmin]

    def get(self, request, pk):
        contribution = get_object_or_404(_visible_contributions(request.user), pk=pk)
        status_map = payments_bridge.payment_status_map(contribution)
        rows = []
        for student in contribution.eligible_students().order_by('username'):
            payment = status_map.get(student.id) or {}
            rows.append(
                {
                    'student': _display_name(student),
                    'matric_number': student.matric_number,
                    'status': payment.get('status', 'pending'),
                    'paid_at': payment.get('paid_at'),
                }
            )
        return Response(rows)