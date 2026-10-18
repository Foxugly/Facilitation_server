"""HttpOnly refresh cookie for the web SPA (opt-in via ``X-Auth-Mode: cookie``).

Fleet design piloted on PushIT (PushIT_server #69): cookie set/absent per header,
refresh + rotation through the cookie, logout and account deletion clearing it,
the Origin check, and the body-token flow left untouched for other clients.
"""
import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import MagicLinkToken, User
from accounts.tokens import email_confirmation_token_generator

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
DELETE_URL = "/api/v1/auth/account/delete/"
MAGIC_VERIFY_URL = "/api/v1/auth/magic-link/verify/"
CONFIRM_URL = "/api/v1/auth/email/confirm/"

ORIGIN = "https://facilitation.foxugly.com"
PASSWORD = "sup3rSecret!"
EMAIL = "cookie@example.com"
COOKIE = "facilitation_refresh"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_allowed_origin")]


@pytest.fixture
def _allowed_origin():
    with override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN]):
        yield


@pytest.fixture
def user():
    return User.objects.create_user(email=EMAIL, password=PASSWORD, email_confirmed=True)


def spa_client(origin=ORIGIN):
    """A browser-like client: cookie opt-in header + Origin on every request."""
    headers = {"HTTP_X_AUTH_MODE": "cookie"}
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    client = APIClient()
    client.credentials(**headers)
    return client


def login(client, **extra):
    return client.post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD, **extra}, format="json")


def is_blacklisted(token: str) -> bool:
    try:
        RefreshToken(token)
    except Exception:
        return True
    return False


# --- cookie set / absent ---------------------------------------------------


def test_login_in_cookie_mode_sets_httponly_cookie_and_omits_refresh(user):
    resp = login(spa_client())

    assert resp.status_code == 200
    assert "refresh" not in resp.json()
    assert resp.json()["access"]
    cookie = resp.cookies[COOKIE]
    assert cookie.value
    assert cookie["httponly"]
    assert cookie["secure"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/api/v1/auth/"
    assert cookie["domain"] == ""  # host-only on the API
    lifetime = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()
    assert int(cookie["max-age"]) == int(lifetime)


def test_login_without_remember_me_sets_a_session_cookie(user):
    resp = login(spa_client(), remember_me=False)

    assert resp.status_code == 200
    assert resp.cookies[COOKIE]["max-age"] == ""


def test_session_cookie_stays_a_session_cookie_after_rotation(user):
    client = spa_client()
    login(client, remember_me=False)

    resp = client.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 200
    assert resp.cookies[COOKIE]["max-age"] == ""


def test_login_without_header_keeps_body_tokens_and_sets_no_cookie(user):
    resp = APIClient().post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD}, format="json")

    assert resp.status_code == 200
    assert resp.json()["refresh"]
    assert COOKIE not in resp.cookies


def test_magic_link_verify_in_cookie_mode_sets_cookie(user):
    link = MagicLinkToken.objects.create(user=user, expires_at=timezone.now() + timezone.timedelta(minutes=15))

    resp = spa_client().post(MAGIC_VERIFY_URL, {"token": link.token}, format="json")

    assert resp.status_code == 200
    assert "refresh" not in resp.json()
    assert resp.cookies[COOKIE].value


def test_email_confirm_in_cookie_mode_sets_cookie():
    pending = User.objects.create_user(email="new@example.com", password=PASSWORD)
    uid = urlsafe_base64_encode(force_bytes(pending.pk))
    token = email_confirmation_token_generator.make_token(pending)

    resp = spa_client().post(CONFIRM_URL, {"uid": uid, "token": token}, format="json")

    assert resp.status_code == 200
    assert "refresh" not in resp.json()
    assert resp.cookies[COOKIE].value


# --- refresh through the cookie ---------------------------------------------


def test_refresh_reads_the_cookie_and_rotates_it(user):
    client = spa_client()
    original = login(client).cookies[COOKIE].value

    resp = client.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 200
    assert resp.json()["access"]
    assert "refresh" not in resp.json()
    rotated = resp.cookies[COOKIE].value
    assert rotated and rotated != original
    assert is_blacklisted(original)


def test_replaying_a_rotated_cookie_is_rejected(user):
    client = spa_client()
    original = login(client).cookies[COOKIE].value
    client.post(REFRESH_URL, {}, format="json")

    replay = spa_client()
    replay.cookies[COOKIE] = original
    resp = replay.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 401


def test_refresh_without_cookie_in_cookie_mode_is_401(user):
    resp = spa_client().post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 401
    assert resp.json()["code"] == "refresh_cookie_missing"


def test_body_token_in_cookie_mode_migrates_to_the_cookie(user):
    """One-time migration: an SPA still holding a refresh in localStorage."""
    legacy = APIClient().post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD}, format="json").json()["refresh"]

    resp = spa_client().post(REFRESH_URL, {"refresh": legacy}, format="json")

    assert resp.status_code == 200
    assert "refresh" not in resp.json()
    assert resp.cookies[COOKIE].value
    assert is_blacklisted(legacy)


# --- Origin check (CSRF) ----------------------------------------------------


@pytest.mark.parametrize("origin", ["https://evil.example", None])
def test_cookie_refresh_from_a_foreign_or_missing_origin_is_403(user, origin):
    cookie = login(spa_client()).cookies[COOKIE].value

    attacker = spa_client(origin=origin)
    attacker.cookies[COOKIE] = cookie
    resp = attacker.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 403
    assert resp.json()["code"] == "origin_not_allowed"
    assert not is_blacklisted(cookie)


def test_cookie_logout_from_a_foreign_origin_is_403(user):
    cookie = login(spa_client()).cookies[COOKIE].value

    attacker = spa_client(origin="https://evil.example")
    attacker.cookies[COOKIE] = cookie
    resp = attacker.post(LOGOUT_URL, {}, format="json")

    assert resp.status_code == 403
    assert not is_blacklisted(cookie)


def test_cookie_login_from_a_foreign_origin_is_403(user):
    resp = login(spa_client(origin="https://evil.example"))

    assert resp.status_code == 403
    assert COOKIE not in resp.cookies


# --- logout ------------------------------------------------------------------


def test_logout_blacklists_the_cookie_and_clears_it_without_bearer(user):
    client = spa_client()
    cookie = login(client).cookies[COOKIE].value

    resp = client.post(LOGOUT_URL, {}, format="json")

    assert resp.status_code == 204
    cleared = resp.cookies[COOKIE]
    assert cleared.value == ""
    assert cleared["path"] == "/api/v1/auth/"
    assert int(cleared["max-age"]) == 0
    assert is_blacklisted(cookie)
    assert client.post(REFRESH_URL, {}, format="json").status_code == 401


def test_logout_in_cookie_mode_is_idempotent_without_a_cookie(user):
    resp = spa_client().post(LOGOUT_URL, {}, format="json")

    assert resp.status_code == 204


# --- account deletion ---------------------------------------------------------


def test_account_deletion_in_cookie_mode_clears_the_cookie(user):
    client = spa_client()
    access = login(client).json()["access"]
    client.credentials(HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN=ORIGIN, HTTP_AUTHORIZATION=f"Bearer {access}")

    resp = client.post(DELETE_URL, {"current_password": PASSWORD}, format="json")

    assert resp.status_code == 204
    assert resp.cookies[COOKIE].value == ""


# --- body flow unchanged ------------------------------------------------------


def test_body_flow_is_unchanged(user):
    app = APIClient()
    refresh = app.post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD}, format="json").json()["refresh"]

    refreshed = app.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert refreshed.status_code == 200
    pair = refreshed.json()
    assert pair["refresh"] != refresh
    assert COOKIE not in refreshed.cookies

    # Body mode still requires the refresh field (400, not a cookie lookup).
    assert app.post(REFRESH_URL, {}, format="json").status_code == 400

    # Body-mode logout still needs a Bearer token and the refresh in the body.
    assert app.post(LOGOUT_URL, {"refresh": pair["refresh"]}, format="json").status_code == 401
    app.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
    assert app.post(LOGOUT_URL, {}, format="json").status_code == 400
    resp = app.post(LOGOUT_URL, {"refresh": pair["refresh"]}, format="json")
    assert resp.status_code == 204
    assert COOKIE not in resp.cookies


def test_cors_preflight_allows_the_opt_in_header_with_credentials():
    resp = APIClient().options(
        REFRESH_URL,
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-auth-mode",
    )

    assert resp["Access-Control-Allow-Origin"] == ORIGIN
    assert resp["Access-Control-Allow-Credentials"] == "true"
    assert "x-auth-mode" in resp["Access-Control-Allow-Headers"]


def test_cors_preflight_from_a_foreign_origin_gets_no_allow_origin():
    resp = APIClient().options(
        REFRESH_URL,
        HTTP_ORIGIN="https://evil.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="x-auth-mode",
    )

    assert "Access-Control-Allow-Origin" not in resp
