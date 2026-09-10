import os


settings_env = os.environ.get("DJANGO_ENV", "").strip().lower()
state = os.environ.get("STATE", "DEV").strip().upper()

if settings_env == "prod" or state == "PROD":
    from .prod import *  # noqa: F401,F403
elif settings_env == "test":
    from .test import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403
