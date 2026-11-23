"""Repo-local scrubbing of what goes to Sentry (sentry-sdk >= 3.0).

sentry-sdk 3.0 removed the built-in ``EventScrubber`` (and ``before_send_transaction``).
``scrub_event`` redoes what it did in ``before_send``; ``scrub_span`` applies the same
denylist to spans in ``before_send_span``. Self-contained on purpose: no SDK internals.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

#: Replaces a sensitive value, as sentry-sdk's own scrubbing does.
FILTERED = "[Filtered]"

# What sentry-sdk 2.x's EventScrubber blanked (DEFAULT_DENYLIST plus
# DEFAULT_PII_DENYLIST, since `send_default_pii` was off), compared like it did:
# lower case, "-" read as "_". sentry-sdk 3.0 removed that scrubber.
_DENYLIST = frozenset(
    key.lower().replace("-", "_")
    for key in (
        "password", "passwd", "secret", "api_key", "apikey", "auth", "credentials",
        "mysql_pwd", "privatekey", "private_key", "token", "session", "csrftoken",
        "sessionid", "x_csrftoken", "set_cookie", "cookie", "authorization",
        "proxy-authorization", "x_api_key", "aiohttp_session", "connect.sid",
        "csrf_token", "csrf", "_csrf", "_csrf_token", "PHPSESSID", "_session",
        "symfony", "user_session", "_xsrf", "XSRF-TOKEN",
        "x_forwarded_for", "x_real_ip", "ip_address", "remote_addr",
    )
)
# A key that merely *contains* one of these is blanked too (`refresh_token`,
# `X-Auth-Token`, `new_password1`...): stricter than 2.x, never looser.
_DENY_PARTS = (
    "password", "passwd", "secret", "token", "apikey", "api_key", "authoriz",
    "credential", "cookie", "session", "csrf", "xsrf", "private_key", "privatekey",
)
# `name=value` pairs in free text (URLs, query strings, span names).
_SENSITIVE_PAIR = re.compile(
    r"(?i)([\w.\-\[\]]*(?:" + "|".join(_DENY_PARTS) + r"|auth)[\w.\-\[\]]*=)[^&\s#;]*"
)
# Path segments that follow one of these carry a one-time secret
# (`/reset-password/<token>/`, `/magic-link/<token>/`...).
_SECRET_ROUTES = frozenset(
    {
        "magic-link", "magic_link", "magic-login", "magiclink", "reset",
        "password-reset", "reset-password", "password_reset", "activate",
        "activation", "invite", "invitation", "invitations", "accept-invite",
        "verify", "confirm", "confirmation", "token", "tokens", "unsubscribe",
        "ticket", "share", "download",
    }
)
# A long opaque segment (token, signed value) is masked wherever it appears.
_OPAQUE = re.compile(r"[A-Za-z0-9_\-.:=~+]{24,}")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")
# A path standing alone (`GET /reset/x/`), not the `//host/...` of a URL.
_PATH = re.compile(r"(?<![:/\w])(?:/[^/\s?#\"'<>]*)+")
_MAX_DEPTH = 20


def is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return normalized in _DENYLIST or any(part in normalized for part in _DENY_PARTS)


def _is_opaque(segment: str) -> bool:
    return bool(_OPAQUE.fullmatch(segment)) and any(c.isdigit() for c in segment)


def _scrub_path(path: str) -> str:
    """Mask the segments of a path that carry a secret."""
    segments = path.split("/")
    for i, segment in enumerate(segments):
        if not segment or segment.startswith(("<", "{")):
            continue  # empty, or a route template (`<token>`): nothing to hide
        after_secret_route = i > 0 and segments[i - 1].lower() in _SECRET_ROUTES
        if after_secret_route or _is_opaque(segment):
            segments[i] = FILTERED
    return "/".join(segments)


def _scrub_url(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        parts = urlsplit(url)
        netloc = parts.netloc.rpartition("@")[2]  # drop user:password@
        return urlunsplit(parts._replace(netloc=netloc, path=_scrub_path(parts.path)))
    except ValueError:
        return FILTERED  # unparseable URL: do not guess what it hides


def scrub_text(text: Any) -> Any:
    """Mask secrets in free text (URLs, query strings, span names, messages).

    `name=value` pairs with a sensitive name, the `user:password@` of a URL, and
    path segments after a secret-bearing route or that look like a token.
    """
    if not isinstance(text, str):
        return text
    if "://" in text:
        text = _URL.sub(_scrub_url, text)
    if "/" in text:
        text = _PATH.sub(lambda m: _scrub_path(m.group(0)), text)
    if "=" in text:
        text = _SENSITIVE_PAIR.sub(lambda m: m.group(1) + FILTERED, text)
    return text


def _scrub(value: Any, depth: int = 0) -> Any:
    """Blank sensitive keys and mask secrets in strings, recursively, in place.

    Returns the scrubbed value (strings are immutable, containers are edited).
    """
    if depth > _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        for key in list(value):
            if is_sensitive_key(key):
                value[key] = FILTERED
            else:
                value[key] = _scrub(value[key], depth + 1)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            value[i] = _scrub(item, depth + 1)
    elif isinstance(value, str):
        return scrub_text(value)
    return value


def _frames(event: dict[str, Any]) -> Iterator[dict[str, Any]]:
    stacktraces = [event.get("stacktrace")]
    for container in ("exception", "threads"):
        values = event.get(container)
        values = values.get("values") if isinstance(values, dict) else None
        if isinstance(values, list):
            stacktraces += [v.get("stacktrace") for v in values if isinstance(v, dict)]
    for stacktrace in stacktraces:
        frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
        if isinstance(frames, list):
            yield from (f for f in frames if isinstance(f, dict))


def scrub_event(event: Any, hint: Any = None) -> Any:
    """`before_send`, first step: what 2.x's EventScrubber did, and a bit more.

    Request headers, cookies, body, URL and query string, `extra`, `contexts`
    (response headers, trace data...), the user (whose IP is dropped), message,
    breadcrumb data, every stack frame's local variables and any embedded span.
    Written so that it cannot raise: in sentry-sdk 3.0 an exception in an event
    callback drops the event.
    """
    if not isinstance(event, dict):
        return event
    request = event.get("request")
    if isinstance(request, dict):
        for part in ("headers", "cookies", "data", "env", "query_string", "url"):
            if part in request:
                request[part] = _scrub(request[part])
    # `contexts` holds response headers, trace data...
    for section in ("extra", "contexts"):
        if section in event:
            event[section] = _scrub(event[section])
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
        _scrub(user)
    for part in ("message", "transaction"):
        if isinstance(event.get(part), str):
            event[part] = scrub_text(event[part])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        _scrub(logentry)
    spans = event.get("spans")
    if isinstance(spans, list):
        for span in spans:
            scrub_span(span)
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else None
    if isinstance(values, list):
        for crumb in values:
            if isinstance(crumb, dict):
                for part in ("data", "message"):
                    if part in crumb:
                        crumb[part] = _scrub(crumb[part])
    for frame in _frames(event):
        _scrub(frame.get("vars"))
    return event


def scrub_span(span: Any, hint: Any = None) -> Any:
    """`before_send_span`: the same scrubbing on the span name and attributes.

    Also used for spans embedded in an event (`name`/`description`, `data`).
    A span cannot be dropped here (sentry-sdk 3.0), only rewritten.
    """
    if not isinstance(span, dict):
        return span
    for part in ("name", "description"):
        if isinstance(span.get(part), str):
            span[part] = scrub_text(span[part])
    for part in ("attributes", "data"):
        attributes = span.get(part)
        if not isinstance(attributes, dict):
            continue
        for key, value in list(attributes.items()):
            last = key.rsplit(".", 1)[-1] if isinstance(key, str) else key
            if is_sensitive_key(key) or is_sensitive_key(last):
                attributes[key] = FILTERED
            else:
                attributes[key] = _scrub(value)
    return span
