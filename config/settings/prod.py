from .base import *

DEBUG = False

# Behind nginx TLS termination (OPERATIONS.md §3.6).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# HSTS is sent by nginx alone (deploy/nginx/facilitation-api.conf), which also
# covers /static/, /media/ and nginx's own error pages. Django sent it a second
# time on every proxied response (audit 2026-10-18, M12). The deploy check that
# flags a zero value is silenced for that reason, not forgotten.
SECURE_HSTS_SECONDS = 0
SILENCED_SYSTEM_CHECKS = ["security.W004"]

# Shared cache: the HTTP API runs in several gunicorn workers (plus daphne). With the
# default per-process LocMem cache, each worker kept its own throttle counters — a
# "10/min" login limit became 10/min per worker. Same Redis database as the channel
# layer and Celery (`REDIS_URL`), under its own key prefix.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://127.0.0.1:6379/0"),
        "KEY_PREFIX": "facilitation-cache",
    }
}
