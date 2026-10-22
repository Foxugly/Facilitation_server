"""Reuse grace for a just-rotated refresh cookie (accounts/refresh_grace.py).

The boot race: the SPA refreshes on load, the server rotates and blacklists the
old token, the user reloads, the browser drops the response and its Set-Cookie,
and the next load presents the old token. Inside the grace window that replay
gets one new pair; everything else keeps the historical 401.
"""
import time
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.core.cache.backends import locmem
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import refresh_grace
from accounts.models import User
from accounts.tokens import revoke_sessions

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"

ORIGIN = "https://facilitation.foxugly.com"
PASSWORD = "sup3rSecret!"
EMAIL = "grace@example.com"
COOKIE = "facilitation_refresh"
BROWSER = "Mozilla/5.0 (grace test)"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_allowed_origin", "_empty_cache")]


@pytest.fixture
def _allowed_origin():
    with override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN], AUTH_REFRESH_REUSE_GRACE_SECONDS=30):
        yield


@pytest.fixture
def _empty_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user():
    return User.objects.create_user(email=EMAIL, password=PASSWORD, email_confirmed=True)


def browser(user_agent=BROWSER, ip="203.0.113.7"):
    client = APIClient()
    client.credentials(HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN=ORIGIN, HTTP_USER_AGENT=user_agent, REMOTE_ADDR=ip)
    return client


def rotated_once(user):
    """Log in, refresh once (rotation), return (old cookie, rotated cookie)."""
    client = browser()
    old = client.post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD}, format="json").cookies[COOKIE].value
    resp = client.post(REFRESH_URL, {}, format="json")
    assert resp.status_code == 200
    return old, resp.cookies[COOKIE].value


def replay(token, **client_kwargs):
    client = browser(**client_kwargs)
    client.cookies[COOKIE] = token
    return client.post(REFRESH_URL, {}, format="json")


def is_live(token: str) -> bool:
    try:
        RefreshToken(token)
    except Exception:
        return False
    return True


def test_reuse_inside_the_window_gets_one_new_pair(user):
    old, lost = rotated_once(user)

    resp = replay(old)

    assert resp.status_code == 200
    assert resp.json()["access"]
    assert "refresh" not in resp.json()
    reissued = resp.cookies[COOKIE].value
    assert reissued not in (old, lost)
    assert resp.cookies[COOKIE]["httponly"]
    assert resp.cookies[COOKIE]["samesite"] == "Lax"
    # Still exactly one live refresh token for the session.
    assert is_live(reissued)
    assert not is_live(lost)
    assert not is_live(old)
    # And the reissued one works normally.
    assert replay(reissued).status_code == 200


def test_second_reuse_inside_the_window_is_401(user):
    old, _ = rotated_once(user)
    assert replay(old).status_code == 200

    assert replay(old).status_code == 401


def test_third_reuse_is_401(user):
    old, _ = rotated_once(user)
    replay(old)
    replay(old)

    assert replay(old).status_code == 401


def test_reuse_after_the_window_is_401(user, monkeypatch):
    old, lost = rotated_once(user)
    later = time.time() + 31
    monkeypatch.setattr(locmem, "time", SimpleNamespace(time=lambda: later))

    resp = replay(old)

    assert resp.status_code == 401
    assert is_live(lost)  # the failed replay does not touch the live token


def test_reuse_after_the_successor_was_used_is_401(user):
    """The client got the rotated cookie and moved on: a replay is not the race."""
    old, lost = rotated_once(user)
    assert replay(lost).status_code == 200

    assert replay(old).status_code == 401


def test_reuse_after_logout_is_401(user):
    old, lost = rotated_once(user)
    logout = browser()
    logout.cookies[COOKIE] = lost
    assert logout.post(LOGOUT_URL, {}, format="json").status_code in (200, 204, 205)

    assert replay(old).status_code == 401


def test_reuse_after_revocation_is_401(user):
    """Password reset / account deletion revoke every refresh token."""
    old, _ = rotated_once(user)
    revoke_sessions(user)

    assert replay(old).status_code == 401


def test_reuse_for_an_inactive_user_is_401(user):
    old, _ = rotated_once(user)
    User.objects.filter(pk=user.pk).update(is_active=False)

    assert replay(old).status_code == 401


@pytest.mark.parametrize("client_kwargs", [{"user_agent": "curl/8.0"}, {"ip": "198.51.100.9"}])
def test_reuse_from_another_client_is_401_and_burns_the_window(user, client_kwargs):
    old, lost = rotated_once(user)

    assert replay(old, **client_kwargs).status_code == 401
    # The suspicious attempt consumed the entry: the right client gets nothing either.
    assert replay(old).status_code == 401
    assert is_live(lost)


def test_window_disabled_with_zero(user):
    with override_settings(AUTH_REFRESH_REUSE_GRACE_SECONDS=0):
        old, _ = rotated_once(user)

        assert replay(old).status_code == 401


def test_body_mode_has_no_grace(user):
    client = APIClient()
    old = client.post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD}, format="json").json()["refresh"]
    assert client.post(REFRESH_URL, {"refresh": old}, format="json").status_code == 200

    assert client.post(REFRESH_URL, {"refresh": old}, format="json").status_code == 401


def test_forged_token_gets_no_grace(user):
    old, _ = rotated_once(user)
    header, payload, _sig = old.split(".")

    assert replay(f"{header}.{payload}.AAAA").status_code == 401
    # The bad signature did not consume the legitimate entry.
    assert replay(old).status_code == 200


def test_logout_with_the_old_cookie_closes_the_window(user):
    """Signing out with the lost-race cookie must not leave it redeemable."""
    old, lost = rotated_once(user)
    logout = browser()
    logout.cookies[COOKIE] = old
    assert logout.post(LOGOUT_URL, {}, format="json").status_code == 204

    assert replay(old).status_code == 401
    assert not is_live(lost)  # the successor was blacklisted too


def test_logout_with_a_forged_old_cookie_changes_nothing(user):
    old, lost = rotated_once(user)
    header, payload, _sig = old.split(".")
    logout = browser()
    logout.cookies[COOKIE] = f"{header}.{payload}.AAAA"
    logout.post(LOGOUT_URL, {}, format="json")

    assert is_live(lost)
    assert replay(old).status_code == 200


class _BrokenCache:
    """Cache whose writes fail (Redis down); reads behave as an empty cache."""

    def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    def delete(self, *args, **kwargs):
        raise ConnectionError("redis down")


def test_cache_error_during_rotation_still_returns_the_rotated_cookie(user, monkeypatch, caplog):
    client = browser()
    old = client.post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD}, format="json").cookies[COOKIE].value
    monkeypatch.setattr(refresh_grace, "cache", _BrokenCache())

    resp = client.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 200
    rotated = resp.cookies[COOKIE].value
    assert rotated and rotated != old
    assert is_live(rotated)
    assert "refresh_grace_cache_set_failed" in caplog.text
    assert old not in caplog.text and rotated not in caplog.text
    # Without the cache there is no grace, and a replay is the usual 401, not a 500.
    assert replay(old).status_code == 401


def test_grace_loses_to_a_successor_blacklisted_concurrently(user, monkeypatch):
    """The successor gets blacklisted between the claim and the reissue (a
    concurrent rotation): the unique blacklist row decides, no second live token."""
    old, lost = rotated_once(user)
    real = refresh_grace._blacklist_if_live

    def racing(successor_jti):
        RefreshToken(lost).blacklist()  # the other request wins first
        return real(successor_jti)

    monkeypatch.setattr(refresh_grace, "_blacklist_if_live", racing)

    assert replay(old).status_code == 401
