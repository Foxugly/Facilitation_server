from django.db import connection
from django.http import JsonResponse


def health(request):
    """Liveness + DB check (OPERATIONS.md §3.9). UptimeRobot asserts the keyword."""
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False

    status = "ok" if db_ok else "degraded"
    return JsonResponse(
        {"status": status, "database": "ok" if db_ok else "error"},
        status=200 if db_ok else 503,
    )
