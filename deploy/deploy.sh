#!/usr/bin/env bash
# =============================================================================
# Delegation Poker — Deployment script (runs as 'django' via OIDC->SSM).
#   /var/www/django_websites/Poker_server/deploy/deploy.sh
# =============================================================================
set -euo pipefail
umask 027   # new dirs 750 / files 640 from git/pip/collectstatic (§3.1/§3.2)

APP_DIR="/var/www/django_websites/Poker_server"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

# Load the SSM-fetched env so manage.py has SECRET_KEY, STATE, DB creds, etc.
# Parse literally (key=value), NOT `source`: values may contain shell-special
# chars that `.` would mangle (mirrors systemd EnvironmentFile parsing).
ENV_FILE="/run/poker/.env"
if [ -f "$ENV_FILE" ]; then
    echo ">>> Loading env from $ENV_FILE..."
    while IFS='=' read -r _k _v || [ -n "$_k" ]; do
        case "$_k" in ''|\#*) continue ;; esac
        export "$_k=$_v"
    done < "$ENV_FILE"
    unset _k _v
else
    echo "WARNING: $ENV_FILE missing — has facilitation-env-fetch run? Trying without it." >&2
fi

echo ">>> Running migrations..."
"$VENV/bin/python" manage.py migrate --noinput

echo ">>> Collecting static files..."
"$VENV/bin/python" manage.py collectstatic --noinput

echo ">>> Seeding the standard Delegation Poker deck (idempotent)..."
"$VENV/bin/python" manage.py seed_delegation_deck || true

echo ">>> Normalizing permissions (dirs 750 / files 640, no o-rwx, no g-w)..."
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

# facilitation-env-fetch is intentionally NOT restarted here (a code deploy keeps the
# env already in /run/poker/.env). To pick up changed SSM values:
#   sudo systemctl restart facilitation-env-fetch && sudo systemctl restart facilitation-asgi facilitation-celery facilitation-celery-beat
echo ">>> Restarting services..."
sudo /bin/systemctl restart facilitation-asgi
sudo /bin/systemctl restart facilitation-celery
sudo /bin/systemctl restart facilitation-celery-beat

echo ">>> Deploy complete."
