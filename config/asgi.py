"""ASGI entrypoint — HTTP via Django, WebSocket via Channels (OPERATIONS.md README §4).

This is the single fleet site that runs under an ASGI server (daphne/uvicorn) rather
than gunicorn/WSGI: the realtime brick is isolated here and in the ``realtime`` app.
"""
import os
from urllib.parse import urlparse

from django.core.asgi import get_asgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialise Django (populate apps) before importing anything that touches models.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import OriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402

from realtime.routing import websocket_urlpatterns  # noqa: E402


def _ws_allowed_origins():
    """Accept WS from the SPA origin. The frontend (facilitation.foxugly.com) is a different
    host than the API (facilitation-api.foxugly.com), so validating against ALLOWED_HOSTS
    alone would reject it — validate against the CORS origins (which name the SPA),
    plus ALLOWED_HOSTS for same-host access."""
    origins = set()
    for origin in getattr(settings, "CORS_ALLOWED_ORIGINS", []):
        host = urlparse(origin).hostname
        if host:
            origins.add(host)
    origins.update(h for h in settings.ALLOWED_HOSTS if h and h != "*")
    return sorted(origins) or ["*"]


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": OriginValidator(
            URLRouter(websocket_urlpatterns),
            _ws_allowed_origins(),
        ),
    }
)
