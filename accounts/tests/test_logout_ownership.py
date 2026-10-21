"""Audit 2026-10-18, M8 : le logout ne revoque que le refresh de l'appelant.

Avant, un Bearer du compte A suffisait a blacklister le refresh du compte B
(il suffisait de le connaitre). Un jeton etranger recoit la meme reponse
qu'un jeton invalide, pour ne pas confirmer qu'il est vivant.
"""
import pytest
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User

LOGOUT_URL = "/api/v1/auth/logout/"
ORIGIN = "https://facilitation.foxugly.com"

pytestmark = pytest.mark.django_db


def _blacklisted(refresh):
    return BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()


@pytest.fixture
def alice():
    return User.objects.create_user(email="alice@example.com", password="sup3rSecret!", email_confirmed=True)


@pytest.fixture
def bob():
    return User.objects.create_user(email="bob@example.com", password="sup3rSecret!", email_confirmed=True)


def _client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def test_logout_revokes_the_callers_own_refresh(alice):
    refresh = RefreshToken.for_user(alice)

    resp = _client_for(alice).post(LOGOUT_URL, {"refresh": str(refresh)}, format="json")

    assert resp.status_code == 204
    assert _blacklisted(refresh)


def test_logout_refuses_another_users_refresh(alice, bob):
    bobs_refresh = RefreshToken.for_user(bob)

    resp = _client_for(alice).post(LOGOUT_URL, {"refresh": str(bobs_refresh)}, format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "refresh_token_invalid"
    assert not _blacklisted(bobs_refresh)


@override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN])
def test_cookie_mode_with_bearer_does_not_revoke_another_users_refresh(alice, bob):
    bobs_refresh = RefreshToken.for_user(bob)
    client = _client_for(alice)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(alice).access_token}",
        HTTP_X_AUTH_MODE="cookie",
        HTTP_ORIGIN=ORIGIN,
    )

    resp = client.post(LOGOUT_URL, {"refresh": str(bobs_refresh)}, format="json")

    # Le navigateur d'Alice est deconnecte (cookie efface), mais le jeton de Bob vit.
    assert resp.status_code == 204
    assert not _blacklisted(bobs_refresh)
