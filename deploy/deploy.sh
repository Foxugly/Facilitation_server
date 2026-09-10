#!/usr/bin/env bash
# =============================================================================
# Facilitation — Deployment script (runs as 'django' via OIDC->SSM).
#   /var/www/django_websites/Facilitation_server/deploy/deploy.sh
# =============================================================================
set -euo pipefail
umask 027   # new dirs 750 / files 640 from git/pip/collectstatic (§3.1/§3.2)

APP_DIR="/var/www/django_websites/Facilitation_server"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

# Load the SSM-fetched env so manage.py has SECRET_KEY, STATE, DB creds, etc.
# Parse literally (key=value), NOT `source`: values may contain shell-special
# chars that `.` would mangle (mirrors systemd EnvironmentFile parsing).
ENV_FILE="/run/facilitation/.env"
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
# env already in /run/facilitation/.env). To pick up changed SSM values:
#   sudo systemctl restart facilitation-env-fetch && sudo systemctl restart facilitation-asgi facilitation-celery facilitation-celery-beat
echo ">>> Restarting services..."
sudo /bin/systemctl restart facilitation-asgi

# Celery n'est redemarre QUE s'il est active. Le 2026-09-10, son demarrage a
# sature la box : 1,9 Go de RAM pour dix applications Django, swap plein
# (2047/2047), load a 76, toute la flotte injoignable pendant ~15 min. Les deux
# units consommaient a elles seules 259 Mo. Elles ont ete desactivees.
#
# Sans cette garde, chaque deploiement les relancerait et reproduirait la panne,
# alors meme qu'un operateur les a explicitement arretees. Le script respecte
# donc l'etat choisi hors bande plutot que de l'ecraser.
#
# Pour les reactiver une fois la box redimensionnee :
#   sudo systemctl enable --now facilitation-celery facilitation-celery-beat
for unit in facilitation-celery facilitation-celery-beat; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        sudo /bin/systemctl restart "$unit"
    else
        echo ">>> $unit desactive — non redemarre (voir CLAUDE.md)"
    fi
done

echo ">>> Deploy complete."
