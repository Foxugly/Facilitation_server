"""L'adresse du client derrière nginx : les limites de débit et Turnstile.

nginx est le seul proxy (`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`) :
il **ajoute** l'adresse qu'il voit à droite de ce que le client a envoyé. Seule la
dernière valeur est donc fiable ; tout ce qui la précède est écrit par le client.
Sans `NUM_PROXIES`, DRF prenait la chaîne entière comme identité : un
`X-Forwarded-For` différent à chaque requête, et la limite de connexion ne
s'appliquait jamais.
"""

from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import AnonRateThrottle

from accounts.turnstile import get_remote_ip


def requete(xff=None):
    entetes = {"REMOTE_ADDR": "127.0.0.1"}
    if xff is not None:
        entetes["HTTP_X_FORWARDED_FOR"] = xff
    return APIRequestFactory().post("/api/v1/auth/login/", **entetes)


def ident(xff=None):
    return AnonRateThrottle().get_ident(requete(xff))


def test_un_seul_proxy_est_declare():
    assert api_settings.NUM_PROXIES == 1


def test_un_x_forwarded_for_forge_ne_change_pas_l_identite():
    """Deux requêtes du même client, l'une avec un en-tête inventé : même compteur."""
    vrai = ident("203.0.113.7")
    forge = ident("1.2.3.4, 203.0.113.7")
    assert vrai == forge == "203.0.113.7"


def test_sans_en_tete_on_garde_l_adresse_du_socket():
    assert ident() == "127.0.0.1"


def test_turnstile_recoit_l_adresse_vue_par_nginx_pas_celle_du_client():
    assert get_remote_ip(requete("1.2.3.4, 203.0.113.7")) == "203.0.113.7"
    assert get_remote_ip(requete("203.0.113.7")) == "203.0.113.7"
    assert get_remote_ip(requete()) == "127.0.0.1"
