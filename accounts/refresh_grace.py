"""Grace window for the refresh token the server has just rotated (cookie mode only).

The boot race: the SPA calls ``token/refresh/`` on load; the server rotates the
cookie and blacklists the old token. If the user reloads or navigates while that
response is in flight, the browser aborts the request and may drop its
``Set-Cookie``. The next load then presents the old, now blacklisted token and
the user is signed out. Fail-closed, but it happens to real users.

A frontend change cannot fix this: the access token lives in memory only, so a
fresh page has nothing but the cookie and must refresh.

The fix, kept as narrow as possible:

* each cookie-mode rotation records, in the shared cache (Redis in prod),
  ``old jti -> successor jti + client fingerprint`` for
  ``AUTH_REFRESH_REUSE_GRACE_SECONDS`` (30 s by default);
* a blacklisted refresh token presented again is accepted **once**, inside that
  window, only if: its signature and expiry are valid, the entry is still there
  (``cache.delete`` is the atomic claim, so two concurrent replays cannot both
  win), the request comes from the same client (IP + User-Agent), the successor
  was never used or revoked (still outstanding and not blacklisted: a logout, a
  password reset or a further rotation all close the window), and the user is
  still allowed to sign in;
* the successor is then blacklisted and a new pair is issued, so there is still
  exactly one live refresh token for the session; the reissue opens no new
  window.

Outside those conditions the replay gets the historical 401: rotation, the
blacklist and replay rejection are unchanged.
"""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework_simplejwt.exceptions import TokenBackendError, TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

from .turnstile import get_remote_ip

KEY_PREFIX = "auth:refresh-grace:"


def grace_seconds() -> int:
    return int(getattr(settings, "AUTH_REFRESH_REUSE_GRACE_SECONDS", 30))


def _key(jti: str) -> str:
    return f"{KEY_PREFIX}{jti}"


def _fingerprint(request) -> str:
    raw = f"{get_remote_ip(request) or ''}|{request.META.get('HTTP_USER_AGENT', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _jti(raw_token: str) -> str | None:
    # Only for tokens the serializer has just verified (old) or minted (new).
    return RefreshToken(raw_token, verify=False).payload.get(jwt_settings.JTI_CLAIM)


def remember_rotation(request, old_token: str, new_token: str) -> None:
    """Open the grace window for ``old_token`` (just rotated into ``new_token``)."""
    seconds = grace_seconds()
    if seconds <= 0:
        return
    old_jti, new_jti = _jti(old_token), _jti(new_token)
    if not old_jti or not new_jti:
        return
    cache.set(_key(old_jti), {"successor": new_jti, "fp": _fingerprint(request)}, seconds)


def _verified_refresh_payload(raw_token: str) -> dict | None:
    """Signature, expiry and token type, deliberately without the blacklist check."""
    try:
        backend = RefreshToken(raw_token, verify=False).get_token_backend()
        payload = backend.decode(raw_token, verify=True)
    except (TokenError, TokenBackendError):
        return None
    if payload.get(jwt_settings.TOKEN_TYPE_CLAIM) != RefreshToken.token_type:
        return None
    return payload


def redeem(request, raw_token: str) -> dict | None:
    """New ``{"access", "refresh"}`` pair for a just-rotated token inside its
    grace window, or None (the caller then answers the usual 401)."""
    payload = _verified_refresh_payload(raw_token)
    if payload is None or not payload.get(jwt_settings.JTI_CLAIM):
        return None
    key = _key(payload[jwt_settings.JTI_CLAIM])
    entry = cache.get(key)
    # Claim the entry before anything else: single use, whatever happens next.
    if not entry or not cache.delete(key):
        return None
    if entry.get("fp") != _fingerprint(request):
        return None

    successor = OutstandingToken.objects.filter(jti=entry.get("successor")).first()
    if successor is None or BlacklistedToken.objects.filter(token=successor).exists():
        return None  # logout, password reset or a later rotation: the session moved on

    user_id = payload.get(jwt_settings.USER_ID_CLAIM)
    user = get_user_model().objects.filter(**{jwt_settings.USER_ID_FIELD: user_id}).first()
    if user is None or not jwt_settings.USER_AUTHENTICATION_RULE(user):
        return None

    BlacklistedToken.objects.get_or_create(token=successor)
    refresh = RefreshToken(raw_token, verify=False)  # same claims (persist, user)
    refresh.set_jti()
    refresh.set_exp()
    refresh.set_iat()
    refresh.outstand()
    return {"access": str(refresh.access_token), "refresh": str(refresh)}
