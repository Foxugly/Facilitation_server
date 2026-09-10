#!/usr/bin/env bash
# =============================================================================
# Delegation Poker — Seed AWS SSM /poker/prod/* (run OFF-BOX, admin identity).
#
# Bare names (§3.5/§3.14). Real secrets = SecureString; everything else = String.
# Fill the <PLACEHOLDER> values before running. Idempotent: re-run to update.
# =============================================================================
set -euo pipefail
REGION="eu-west-1"
P="/poker/prod"

put()   { aws ssm put-parameter --region "$REGION" --name "$P/$1" --type String       --overwrite --value "$2"; }
secret(){ aws ssm put-parameter --region "$REGION" --name "$P/$1" --type SecureString --overwrite --value "$2"; }

# --- Runtime / env ---
put STATE "PROD"
put DEBUG "False"
put ALLOWED_HOSTS "facilitation-api.foxugly.com"
put CORS_ALLOWED_ORIGINS "https://facilitation.foxugly.com"
put CSRF_TRUSTED_ORIGINS "https://facilitation.foxugly.com,https://facilitation-api.foxugly.com"
put FRONTEND_BASE_URL "https://facilitation.foxugly.com"
put PUBLIC_MEDIA_BASE_URL "https://facilitation-api.foxugly.com"

# --- Database (box-local PostgreSQL, DB_* 6-var convention §3.13) ---
put DB_ENGINE "postgresql"
put DB_HOST "127.0.0.1"
put DB_PORT "5432"
put DB_NAME "poker"
put DB_USER "poker"
secret DB_PASSWORD "<DB_PASSWORD>"

# --- Redis (Channels transport + Celery broker; already on the box) ---
put REDIS_URL "redis://127.0.0.1:6379/3"

# --- Secrets ---
secret SECRET_KEY "<DJANGO_SECRET_KEY>"

# --- Sentry (poker-backend project; DSN is public — String, §3.14) ---
put SENTRY_DSN "<SENTRY_BACKEND_DSN>"
put SENTRY_ENVIRONMENT "PROD"
put SENTRY_TRACES_SAMPLE_RATE "0.0"

echo "Seeded $P/* — remember to grant the instance role foxugly-fleet-ec2 SSM read on $P and $P/*."
