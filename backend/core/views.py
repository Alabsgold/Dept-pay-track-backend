from django.http import JsonResponse
from django.db import connection
from rest_framework.views import APIView


class HealthView(APIView):
    """
    GET /api/health/ — unauthenticated liveness + DB-reachability probe for
    the platform (Render health checks, the demo, and ops). Deliberately
    returns no domain data.
    """

    permission_classes = []

    def get(self, request):
        try:
            connection.ensure_connection()
        except Exception:
            return JsonResponse({'status': 'unhealthy', 'database': 'down'}, status=503)
        return JsonResponse({'status': 'ok', 'database': 'up'})