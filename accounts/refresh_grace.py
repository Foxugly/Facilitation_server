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

Hardening:

* a logout with the old cookie closes its window and blacklists the successor
  (``forget``), so the old cookie cannot be redeemed after a sign-out;
* the "successor still live" check and its blacklisting run in one transaction
  holding a row lock on the successor's ``OutstandingToken``; the refresh view
  takes the same lock on the presented token before rotating it, so a
  concurrent rotation of the successor and a grace reissue are serialised and
  cannot both leave a live token;
* the cache is best effort: an error while opening a window is logged and the
  rotation still succeeds; an error while redeeming answers the usual 401.
"""
from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from rest_framework_simplejwt.exceptions import TokenBackendError, TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

from .turnstile import get_remote_ip

logger = logging.getLogger("facilitation")

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
    try:
        cache.set(_key(old_jti), {"successor": new_jti, "fp": _fingerprint(request)}, seconds)
    except Exception:
        # Never turn a successful rotation into a 500 (the client would lose the
        # rotated cookie, i.e. the session). No token data in the log.
        logger.warning("refresh_grace_cache_set_failed", exc_info=True)


def lock_outstanding(raw_token: str) -> None:
    """Row-lock the token's ``OutstandingToken`` for the current transaction.

    Taken by the refresh view before rotating and by ``redeem`` before
    reissuing, so the two never decide on the same token concurrently.
    """
    try:
        jti = RefreshToken(raw_token, verify=False).payload.get(jwt_settings.JTI_CLAIM)
    except TokenError:
        return
    if jti:
        list(OutstandingToken.objects.select_for_update().filter(jti=jti).values_list("pk", flat=True))


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


def _claim(key: str) -> dict | None:
    """Read and delete the entry; whoever deletes it owns it (single use)."""
    try:
        entry = cache.get(key)
        if not entry or not cache.delete(key):
            return None
    except Exception:
        logger.warning("refresh_grace_cache_unavailable", exc_info=True)
        return None
    return entry


def _blacklist_if_live(successor_jti: str | None) -> bool:
    """Blacklist the successor if it is still live; False if it already moved on.

    Must run inside ``transaction.atomic``: the successor row is locked first.
    """
    successor = OutstandingToken.objects.select_for_update().filter(jti=successor_jti).first()
    if successor is None:
        return False
    _, created = BlacklistedToken.objects.get_or_create(token=successor)
    return created


def redeem(request, raw_token: str) -> dict | None:
    """New ``{"access", "refresh"}`` pair for a just-rotated token inside its
    grace window, or None (the caller then answers the usual 401)."""
    payload = _verified_refresh_payload(raw_token)
    if payload is None or not payload.get(jwt_settings.JTI_CLAIM):
        return None
    # Claim the entry before anything else: single use, whatever happens next.
    entry = _claim(_key(payload[jwt_settings.JTI_CLAIM]))
    if entry is None or entry.get("fp") != _fingerprint(request):
        return None

    user_id = payload.get(jwt_settings.USER_ID_CLAIM)
    user = get_user_model().objects.filter(**{jwt_settings.USER_ID_FIELD: user_id}).first()
    if user is None or not jwt_settings.USER_AUTHENTICATION_RULE(user):
        return None

    with transaction.atomic():
        # Already blacklisted = logout, password reset or a rotation of the
        # successor: the session moved on.
        if not _blacklist_if_live(entry.get("successor")):
            return None
        refresh = RefreshToken(raw_token, verify=False)  # same claims (persist, user)
        refresh.set_jti()
        refresh.set_exp()
        refresh.set_iat()
        refresh.outstand()
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def forget(request, raw_token: str) -> None:
    """Logout with a (possibly already rotated) token: close its grace window and
    blacklist the successor it would have been traded for.

    Signature checked; with a Bearer, only the caller's own token counts (M8).
    """
    payload = _verified_refresh_payload(raw_token)
    if payload is None or not payload.get(jwt_settings.JTI_CLAIM):
        return
    user = request.user
    if user and user.is_authenticated:
        owner = payload.get(jwt_settings.USER_ID_CLAIM)
        if owner is None or str(owner) != str(getattr(user, jwt_settings.USER_ID_FIELD)):
            return
    entry = _claim(_key(payload[jwt_settings.JTI_CLAIM]))
    if entry is None:
        return
    with transaction.atomic():
        _blacklist_if_live(entry.get("successor"))
