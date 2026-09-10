from .base import *  # noqa: F401,F403

DEBUG = False

# Deterministic, dependency-free tests.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# django-parler caches translations in the Django cache, which (LocMem) outlives the
# per-test DB rollback. Primary keys get reused across tests, so a freshly created
# object would read a previous test's translation.
PARLER_ENABLE_CACHING = False

# Effectively disable DRF rate-limiting in tests: the throttle cache (LocMem)
# persists across tests in one process, so repeated register/login calls would
# spuriously 429. Keep every scope key (ScopedRateThrottle KeyErrors on a missing
# one) but set an unreachable rate.
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {k: "100000/min" for k in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]},  # noqa: F405
}
