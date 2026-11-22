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

# The venv must run on $PYTHON (deadsnakes python3.14, root-owned in /usr/bin;
# the system python3 stays 3.12). A venv on another version is rebuilt at its
# final path — never built elsewhere and moved, since console-script shebangs
# (.venv/bin/daphne, gunicorn, celery, used by the units) hard-code the venv
# path. The old one is kept as .venv.prev and put back if the rebuild fails.
# Every unit on this venv is restarted after this script: asgi and enabled
# celery/celery-beat below, gunicorn by root in the SSM command (deploy.yml).
PYTHON=/usr/bin/python3.14
PY_WANT="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_HAVE="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
if [ "$PY_HAVE" != "$PY_WANT" ]; then
    echo ">>> Rebuilding the venv: Python $PY_HAVE -> $PY_WANT..."
    rm -rf "$VENV.prev"
    if [ -e "$VENV" ]; then mv "$VENV" "$VENV.prev"; fi
    if ! { "$PYTHON" -m venv "$VENV"            && "$VENV/bin/pip" install --quiet --require-hashes --no-deps -r requirements.txt            && "$VENV/bin/pip" check; }; then
        echo "ERROR: venv rebuild failed; restoring the previous venv." >&2
        rm -rf "$VENV"
        if [ -e "$VENV.prev" ]; then mv "$VENV.prev" "$VENV"; fi
        exit 1
    fi
fi

echo ">>> Installing dependencies..."
# requirements.txt is a hash-pinned lock (uv pip compile requirements.in, Linux /
# Python 3.14). A plain `pip install -r` never upgrades a package already
# installed inside its range, so prod silently missed releases CI had tested.
# Installing the exact lock makes prod run what CI ran; --no-deps means nothing
# is resolved on the box, and pip check fails the deploy on an incomplete lock.
"$VENV/bin/pip" install --quiet --require-hashes --no-deps -r requirements.txt
"$VENV/bin/pip" check

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

echo ">>> Seeding the Dot Voting deck (idempotent)..."
"$VENV/bin/python" manage.py seed_dot_voting_deck || true

echo ">>> Normalizing permissions (dirs 750 / files 640, no o-rwx, no g-w)..."
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

# facilitation-env-fetch is intentionally NOT restarted here (a code deploy keeps the
# env already in /run/facilitation/.env). To pick up changed SSM values:
#   sudo systemctl restart facilitation-env-fetch && sudo systemctl restart facilitation-asgi facilitation-gunicorn facilitation-celery facilitation-celery-beat
echo ">>> Restarting services..."
# daphne (WebSockets). The HTTP API, facilitation-gunicorn, is restarted by root in the
# SSM command (deploy.yml), right after this script: django's sudoers grant predates it.
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
