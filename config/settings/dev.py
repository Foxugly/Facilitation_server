from .base import *  # noqa: F401,F403

DEBUG = True

# Card images in the frozen deck snapshot are absolute URLs; in dev they must
# point at the local backend, not the prod host (prod default = FRONTEND_BASE_URL).
PUBLIC_MEDIA_BASE_URL = env("PUBLIC_MEDIA_BASE_URL", default="http://127.0.0.1:8000")  # noqa: F405

# No Redis needed for local dev: run Channels on the in-memory layer.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Run Celery tasks synchronously in dev (no worker/broker required).
CELERY_TASK_ALWAYS_EAGER = True

# --- Suite e2e (Playwright) : E2E=1 releve les quotas de debit ---------------
# Chaque scenario e2e cree une salle, et `create_room` est limite a 20/min par IP.
# La suite tenant desormais plusieurs scenarios, et une relance repartant dans la
# meme fenetre d'une minute, le quota finissait par mordre : la creation renvoyait
# 429 et le test echouait sur un timeout de navigation, tres loin de sa cause.
#
# Gate sur une variable d'environnement plutot que desactive en dur : le dev
# ordinaire garde le meme comportement que la production, throttle compris. Seul
# `playwright.config.ts`, qui demarre ce serveur, pose E2E=1.
#
# Les cles sont TOUTES conservees : ScopedRateThrottle leve KeyError sur un scope
# absent (meme raison que dans config/settings/test.py).
if env.bool("E2E", default=False):  # noqa: F405
    REST_FRAMEWORK = {  # noqa: F405
        **REST_FRAMEWORK,  # noqa: F405
        "DEFAULT_THROTTLE_RATES": {k: "100000/min" for k in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]},  # noqa: F405
    }
