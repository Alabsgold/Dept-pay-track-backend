"""
Read-only aggregate endpoints (API_CONTRACT.md §6).

These exist so the Data/AI layer never queries the database directly: it reads
these numbers and phrases them. Deliberately read-only and model-free — no new
tables, no caches, no LLM calls. Every figure is derived from the same model
methods the contribution summary endpoint uses, so the two can never disagree.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contributions import payments_bridge
from apps.contributions.models import Contribution
from apps.users.permissions import IsClassRepOrAdmin


def _visible_contributions(user):
    """Same department scoping rule as the contributions app."""
    qs = Contribution.objects.all()
    if user.department_id:
        return qs.filter(department_id=user.department_id)
    if user.is_staff or user.is_superuser:
        return qs
    return qs.none()


def _money(value):
    """Money always leaves as a 2-decimal string (frontend displays as-is)."""
    return f"{value:.2f}"


def _scope(request):
    """
    Resolve which contributions this request is about.

    Returns `(contributions, error_response)`. `contribution_id` is optional
    for department-wide numbers; when present it must exist inside the caller's
    own department, otherwise 404 (existence is never leaked across
    departments).
    """
    qs = _visible_contributions(request.user)
    raw_id = request.query_params.get('contribution_id')
    if raw_id in (None, ''):
        return list(qs), None

    try:
        contribution_id = int(raw_id)
    except (TypeError, ValueError):
        return None, Response(
            {'error': 'bad_request', 'message': 'contribution_id must be a number.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    contribution = qs.filter(pk=contribution_id).first()
    if contribution is None:
        return None, Response(
            {'error': 'not_found', 'message': 'Contribution not found or not available to you.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return [contribution], None


class _AggregateView(APIView):
    """Class reps/admins only, scoped to their own department."""

    permission_classes = [IsAuthenticated, IsClassRepOrAdmin]


class CollectionStatsView(_AggregateView):
    """
    GET /api/analytics/collection-stats/[?contribution_id=5]

    One fee when scoped, otherwise every fee in the caller's department. Sums
    the existing per-fee model methods so these totals always match
    `/contributions/{id}/summary/`.
    """

    def get(self, request):
        contributions, error = _scope(request)
        if error is not None:
            return error

        total_expected = sum(
            (c.total_expected() for c in contributions), start=0
        )
        total_collected = sum(
            (c.total_collected() for c in contributions), start=0
        )
        outstanding_count = sum(c.outstanding_count() for c in contributions)

        return Response({
            'total_expected': _money(total_expected),
            'total_collected': _money(total_collected),
            'outstanding_count': outstanding_count,
        })


class OutstandingStudentsView(_AggregateView):
    """
    GET /api/analytics/outstanding-students/?contribution_id=5

    Students who have not paid a given fee. `contribution_id` is required —
    "outstanding" is only meaningful against one fee, and inventing a
    department-wide answer across fees of different amounts would be wrong.
    Safe fields only: never emails, phones, or other identities.
    """

    def get(self, request):
        raw_id = request.query_params.get('contribution_id')
        if raw_id in (None, ''):
            return Response(
                {'error': 'bad_request', 'message': 'contribution_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contributions, error = _scope(request)
        if error is not None:
            return error
        contribution = contributions[0]

        paid_ids = payments_bridge.paid_student_ids(contribution)
        rows = [
            {
                'id': student.id,
                'full_name': student.get_full_name() or student.username,
                'matric_number': student.matric_number,
                'level': student.level,
            }
            for student in contribution.eligible_students()
            .exclude(id__in=paid_ids)
            .order_by('matric_number', 'id')
        ]
        return Response(rows)
