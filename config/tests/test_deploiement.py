"""La séparation daphne / gunicorn (2026-10-09) : ce qui la tient, vérifié sur les fichiers.

daphne ne sert plus que les WebSockets ; l'API HTTP passe par gunicorn. Une erreur ici
ne se voit qu'en production — d'où ces garde-fous.
"""

import json
import re
import textwrap
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]


def lire(chemin):
    return (RACINE / chemin).read_text(encoding="utf-8")


def emplacements_nginx():
    """{location: port} du vhost de l'API."""
    texte = lire("deploy/nginx/facilitation-api.conf")
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"location\s+(\S+)\s*\{[^}]*?proxy_pass http://127\.0\.0\.1:(\d+);", texte, re.S)
    }


def commandes_ssm():
    texte = lire(".github/workflows/deploy.yml").replace("\r\n", "\n")
    debut = texte.index("cat > /tmp/ssm-params.json <<'EOF'\n") + len("cat > /tmp/ssm-params.json <<'EOF'\n")
    fin = texte.index("\n          EOF", debut)
    return json.loads(textwrap.dedent(texte[debut:fin]))["commands"]


def test_nginx_envoie_les_websockets_a_daphne_et_le_reste_a_gunicorn():
    assert emplacements_nginx() == {"/ws/": 8009, "/": 8011}


def test_gunicorn_sert_le_wsgi_sur_8011_avec_deux_workers():
    unite = lire("deploy/systemd/facilitation-gunicorn.service")
    assert "--bind 127.0.0.1:8011" in unite
    assert "config.wsgi:application" in unite
    assert "--workers 2" in unite  # la machine manque de mémoire : ne pas augmenter sans la mesurer
    assert "EnvironmentFile=/run/facilitation/.env" in unite


def test_daphne_reste_sur_8009_et_lit_l_adresse_du_client():
    unite = lire("deploy/systemd/facilitation-asgi.service")
    assert "-p 8009" in unite and "config.asgi:application" in unite
    assert "--proxy-headers" in unite


def test_le_deploiement_verifie_gunicorn_avant_de_basculer_nginx():
    commandes = commandes_ssm()
    assert commandes[0] == "set -eu"

    def position(motif):
        return next(i for i, c in enumerate(commandes) if motif in c)

    redemarrage = position("systemctl restart facilitation-gunicorn")
    sante = position("http://127.0.0.1:8011/health/")
    vhost = position("deploy/nginx/facilitation-api.conf > /etc/nginx")
    rechargement = position("systemctl reload nginx")
    assert redemarrage < sante < vhost < rechargement
    assert any("enable" in c and "facilitation-gunicorn" in c for c in commandes)
    assert any("facilitation-gunicorn" in c and "/etc/systemd/system" in c for c in commandes)


def test_la_production_partage_son_cache_entre_les_processus():
    """Plusieurs workers : un cache par processus diviserait les limites de débit."""
    prod = lire("config/settings/prod.py")
    assert "django.core.cache.backends.redis.RedisCache" in prod
