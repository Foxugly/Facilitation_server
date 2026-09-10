from .base import *  # noqa: F401,F403

DEBUG = True

# Card images in the frozen deck snapshot are absolute URLs; in dev they must
# point at the local backend, not the prod host (prod default = FRONTEND_BASE_URL).
PUBLIC_MEDIA_BASE_URL = env("PUBLIC_MEDIA_BASE_URL", default="http://127.0.0.1:8000")  # noqa: F405

# No Redis needed for local dev: run Channels on the in-memory layer.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Run Celery tasks synchronously in dev (no worker/broker required).
CELERY_TASK_ALWAYS_EAGER = True
