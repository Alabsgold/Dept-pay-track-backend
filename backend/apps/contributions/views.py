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
        target_department = serializer.validated_data.get('department')

        if user.role == User.ROLE_ADMIN or user.is_staff or user.is_superuser:
            if not target_department:
                if user.department_id:
                    target_department = user.department
                else:
                    raise serializers.ValidationError(
                        {'department_id': 'System admins must specify a department_id to create a contribution.'}
                    )
        else:
            if not user.department_id:
                raise serializers.ValidationError(
                    {'non_field_errors': 'You must belong to a department before you can create contributions.'}
                )
            target_department = user.department

        # Owner + department are ALWAYS set server-side. The client can never
        # spoof who created it or target another department without admin rights.
        serializer.save(department=target_department, created_by=user)


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
                # Money is always a 2-decimal string (frontend displays as-is).
                'total_expected': f"{contribution.total_expected():.2f}",
                'total_collected': f"{contribution.total_collected():.2f}",
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

    def post(self, request, pk):
        """
        POST /contributions/{id}/payments/ — class rep/admin only.

        Mark a student as paid WITHOUT an online gateway transaction, for when
        a student has paid offline (cash, transfer). The amount ALWAYS comes
        from the contribution, server-side — the student is never asked for a
        price.
        """
        contribution = get_object_or_404(_visible_contributions(request.user), pk=pk)
        matric_number = request.data.get('matric_number')
        if not matric_number:
            return Response(
                {'error': 'bad_request', 'message': 'matric_number is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        student = User.objects.filter(matric_number=matric_number).first()
        if student is None:
            return Response(
                {'error': 'not_found', 'message': 'No student with that matric number.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        # The marked student must belong to THIS contribution's department
        # (prevents a rep from marking a student from another dept on a fee
        # they don't owe).
        if student.department_id != contribution.department_id:
            return Response(
                {'error': 'bad_request', 'message': 'Student is not in this department.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Level-targeted fees too: marking a student who does not owe this fee
        # would bank a success row we then count as collected, inflating the
        # totals with money that was never due.
        if (
            contribution.target_level
            and student.level != contribution.target_level
        ):
            return Response(
                {
                    'error': 'bad_request',
                    'message': 'This contribution does not apply to that student\'s level.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A rep cannot mark themselves as paid — only a real admin may.
        if student.id == request.user.id and request.user.role != User.ROLE_ADMIN:
            return Response(
                {'error': 'forbidden', 'message': 'You cannot mark yourself as paid.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if payments_bridge.already_paid(contribution, student):
            return Response(
                {
                    'error': 'already_paid',
                    'message': 'This student already has a successful payment for this contribution.',
                },
                status=status.HTTP_409_CONFLICT,
            )

        receipt_reference = (request.data.get('receipt_reference') or '').strip()
        if not receipt_reference:
            return Response(
                {
                    'error': 'bad_request',
                    'message': (
                        'receipt_reference is required — quote the teller/receipt '
                        'number so offline payments can be audited.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payment = payments_bridge.mark_manually_paid(
                contribution, student, request.user, receipt_reference
            )
        except ValueError as exc:
            return Response(
                {'error': 'unavailable', 'message': str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'student': _display_name(student),
                'matric_number': student.matric_number,
                'status': payment.status,
                'paid_at': payment.updated_at,
                'method': payment.method,
            },
            status=status.HTTP_201_CREATED,
        )