import os
from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab


BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

# --- Database: fleet DB_* 6-var convention on box-local PostgreSQL (OPERATIONS.md §3.13) ---
SQLITE_NAME = Path(env("SQLITE_NAME", default=str(BASE_DIR / "db.sqlite3")))
DATABASE_NAME = env("DB_NAME", default="").strip() or str(SQLITE_NAME)
_DB_ENGINE_ALIASES = {
    "sqlite3": "django.db.backends.sqlite3",
    "postgresql": "django.db.backends.postgresql",
    "postgres": "django.db.backends.postgresql",
}
_db_engine = env("DB_ENGINE", default="sqlite3")

SECRET_KEY = env("SECRET_KEY", default="dev-secret-key")
STATE = env("STATE", default="DEV")
DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:4200", "http://127.0.0.1:4200"],
)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Public base URL of the Angular SPA (share links, future emails).
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="https://facilitation.foxugly.com")

INSTALLED_APPS = [
    # daphne must precede staticfiles so runserver uses the ASGI server (Channels).
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "corsheaders",
    "parler",
    "django_extensions",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "accounts.apps.AccountsConfig",
    "teams.apps.TeamsConfig",
    "decks.apps.DecksConfig",
    "rooms.apps.RoomsConfig",
    "realtime.apps.RealtimeConfig",
    "history.apps.HistoryConfig",
    "boards.apps.BoardsConfig",
    "billing.apps.BillingConfig",
    "health.apps.HealthConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": _DB_ENGINE_ALIASES.get(_db_engine, _db_engine),
        "NAME": DATABASE_NAME,
        "HOST": env("DB_HOST", default=""),
        "PORT": env("DB_PORT", default=""),
        "USER": env("DB_USER", default=""),
        "PASSWORD": env("DB_PASSWORD", default=""),
    }
}

# --- Channels (WebSocket transport). Redis in prod; dev/test override to in-memory. ---
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "accounts.auth_backend.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# --- i18n / languages: ONE source of truth (scope §10 extensibility) ---
# Adding a language = one entry here (+ a Transloco catalog on the SPA) — no schema
# migration: card text lives in parler translation rows keyed by language_code.
LANGUAGE_CODE = "en"
TIME_ZONE = "Europe/Brussels"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("fr", "Français"),
    ("nl", "Nederlands"),
    ("en", "English"),
    ("it", "Italiano"),
    ("es", "Español"),
]
PARLER_DEFAULT_LANGUAGE_CODE = "en"
PARLER_LANGUAGES = {
    None: tuple({"code": code} for code, _ in LANGUAGES),
    "default": {"fallbacks": ["en"], "hide_untranslated": False},
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / env("MEDIA_ROOT_DIR", default="media")
# Public origin where MEDIA_URL is served (this API host). Used off-request to
# build absolute card-image URLs baked into the deck snapshot.
PUBLIC_MEDIA_BASE_URL = env("PUBLIC_MEDIA_BASE_URL", default=FRONTEND_BASE_URL)

# --- Rooms lifecycle ---
# Free anonymous rooms expire after this many hours of inactivity (scope §4).
ROOM_INACTIVITY_HOURS = env.int("ROOM_INACTIVITY_HOURS", default=8)
# Hard caps (product): people per room, and members per team (a team feeds a room).
# 15 et non 20 : au-dela, les cartes deviennent trop petites autour de la table pour
# rester lisibles. Le tour de table repartit les sieges sur un ovale, donc chaque
# joueur supplementaire retrecit toutes les cartes — mesure a 1536x864 : 124px a 5
# joueurs, 85 a 12, 61 a 15, 46 a 20. Distinct de TEAM_MAX_MEMBERS ci-dessous, qui
# limite les membres d'une equipe et non les presents dans une salle.
ROOM_MAX_PARTICIPANTS = env.int("ROOM_MAX_PARTICIPANTS", default=15)
TEAM_MAX_MEMBERS = env.int("TEAM_MAX_MEMBERS", default=20)
# Facilitator absence before the takeover guard opens (contract §6.f), in seconds.
FACILITATOR_GUARD_SECONDS = env.int("FACILITATOR_GUARD_SECONDS", default=60)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# --- Celery (async: future email/export; independent of the ASGI/WS brick) ---
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Europe/Brussels"
CELERY_BEAT_SCHEDULE = {
    # Sweep rooms idle > 8h and flag them expired (scope §4).
    "facilitation-expire-stale-rooms": {
        "task": "rooms.tasks.expire_stale_rooms",
        "schedule": crontab(minute="*/15"),
    },
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "create_room": "20/min",
        "join_room": "60/min",
        "login": "10/min",
        "register": "5/min",
        "password_reset": "5/min",
        "resend": "3/min",
        "magic_link": "5/min",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
}

# --- JWT (Phase 2 auth). Rotation + blacklist: every refresh issues a new refresh
# token and blacklists the old — clients MUST persist the rotated token. ---
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=90)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- Email (transactional: confirm / reset / magic-link). Console in dev; prod
# sets EMAIL_BACKEND (SMTP/Graph) + DEFAULT_FROM_EMAIL via SSM. ---
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Facilitation <noreply@facilitation.foxugly.com>")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
MAGIC_LINK_TTL_MINUTES = env.int("MAGIC_LINK_TTL_MINUTES", default=15)

# Microsoft Graph app-only email (fleet standard, §3.14 GRAPH_* names). When set,
# accounts.mail routes through Graph; otherwise it falls back to EMAIL_BACKEND.
GRAPH_TENANT_ID = env("GRAPH_TENANT_ID", default="")
GRAPH_CLIENT_ID = env("GRAPH_CLIENT_ID", default="")
GRAPH_CLIENT_SECRET = env("GRAPH_CLIENT_SECRET", default="")
GRAPH_SENDER = env("GRAPH_SENDER", default="")  # the "from" mailbox (e.g. noreply@foxugly.com)

# --- Cloudflare Turnstile (captcha on register / forgot / magic-link). Gated on
# the secret: skipped until TURNSTILE_SECRET_KEY is set, then fail-closed. ---
TURNSTILE_SITE_KEY = env("TURNSTILE_SITE_KEY", default="")
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", default="")

# --- Facturation : Poker est CONSOMMATEUR du service central (lot L4) ---------
# Poker ne détient plus aucune clé Stripe. Il délègue à billing-api.foxugly.com,
# signé en HMAC, et reçoit en retour des droits poussés qu'il met en cache.
# Gaté sur ces deux variables : tant qu'elles sont absentes, la facturation est
# inerte — les équipes restent ouvertes et le checkout répond 503. C'est ce qui
# permet de déployer cette migration sans rien changer au comportement.
BILLING_BASE_URL = env("BILLING_BASE_URL", default="")
BILLING_APP_SECRET = env("BILLING_APP_SECRET", default="")
BILLING_APP_SLUG = env("BILLING_APP_SLUG", default="facilitation")

# Repli local des quotas, si le central n'en a pas encore poussé pour un plan.
PLAN_QUOTAS = {"team1": 1, "team5": 5}

SPECTACULAR_SETTINGS = {
    "TITLE": "Facilitation API",
    "DESCRIPTION": "Backend for Facilitation (rooms + activities + realtime).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sentry (only active under PROD; mirrors the __init__ dispatch) ---
_SENTRY_PROD_ACTIVE = (
    os.environ.get("DJANGO_ENV", "").strip().lower() == "prod"
    or STATE.strip().upper() == "PROD"
)
SENTRY_DSN = env("SENTRY_DSN", default="")
# L'etiquette et la garde doivent decouler de la MEME valeur. Elles divergeaient :
# la garde acceptait `DJANGO_ENV=prod` OU `STATE=PROD`, mais l'etiquette valait
# `STATE`. Un poste local avec DJANGO_ENV=prod et STATE=DEV passait donc la garde
# et envoyait dans le projet Sentry DE PRODUCTION des evenements etiquetes DEV
# (constate le 2026-07-31 sur pushit : 33 occurrences depuis juin).
#
# En exigeant que l'etiquette resolue soit elle-meme un marqueur de production,
# emettre un evenement etiquete autrement devient impossible. Production
# inchangee : STATE=PROD est bien pose dans /run/<app>/.env.
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default="") or STATE
_SENTRY_ENV_IS_PROD = SENTRY_ENVIRONMENT.strip().upper() in {"PROD", "PRODUCTION"}
if SENTRY_DSN and _SENTRY_PROD_ACTIVE and _SENTRY_ENV_IS_PROD:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        release=env("SENTRY_RELEASE", default=None),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        profiles_sample_rate=env.float("SENTRY_PROFILES_SAMPLE_RATE", default=0.0),
        send_default_pii=env.bool("SENTRY_SEND_PII", default=False),
    )
