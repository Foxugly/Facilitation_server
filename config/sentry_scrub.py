"""Repo-local scrubbing of what goes to Sentry (sentry-sdk >= 3.0).

sentry-sdk 3.0 removed the built-in ``EventScrubber`` (and ``before_send_transaction``).
``scrub_event`` redoes what it did in ``before_send``; ``scrub_span`` applies the same
denylist to spans in ``before_send_span``. Self-contained on purpose: no SDK internals.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote_plus

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
    "ticket", "signature",
)
# `name=value` pairs in free text and query strings.
_PAIR = re.compile(r"([\w.\-\[\]]+)=([^&\s#;\"'<>]*)")
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
# What a token, signed value or key looks like once in a URL or a message.
_OPAQUE = re.compile(r"[A-Za-z0-9_\-.:=~+%]+")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")
# A path standing alone (`GET /reset/x/`), not the `//host/...` of a URL.
_PATH = re.compile(r"(?<![:/\w\]])(?:/[^/\s?#\"'<>]*)+")
# `host`, `host:port`, `[v6]` or `[v6]:port`: anything else after `://` hides
# something (a `user:password` without `@`, for instance).
_HOST = re.compile(r"(?:\[[0-9A-Fa-f:.]*\]|[^:@\[\]]*)(?::\d*)?")
_AUTHORITY = re.compile(r"[^/?#]*")
_MAX_DEPTH = 20


def is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return normalized in _DENYLIST or any(part in normalized for part in _DENY_PARTS)


def _is_opaque(value: str) -> bool:
    """A long random-looking value: hex/base64 token, signed value, key."""
    if len(value) < 16 or not _OPAQUE.fullmatch(value):
        return False
    if len(value) >= 32 or any(c.isdigit() for c in value):
        return True
    return any(c.isupper() for c in value) and any(c.islower() for c in value)


def _is_sensitive_param(name: str, value: str) -> bool:
    return is_sensitive_key(name) or "auth" in name.lower() or _is_opaque(value)


def _scrub_pair(match: re.Match[str]) -> str:
    name, value = match.group(1), match.group(2)
    if _is_opaque(name):
        return FILTERED + "=" + FILTERED
    if _is_sensitive_param(name, value):
        return name + "=" + FILTERED
    return match.group(0)


def _scrub_query_chunk(chunk: str) -> str:
    # Checked decoded *and* raw; the raw text is what is kept, so nothing is
    # re-encoded differently from what came in.
    name, equals, value = chunk.partition("=")
    plain_name, plain_value = unquote_plus(name), unquote_plus(value)
    if _is_opaque(name) or _is_opaque(plain_name):
        return FILTERED + equals + (FILTERED if equals else "")
    if not equals:
        return chunk  # a bare flag (`?debug`) that does not look like a token
    if _is_sensitive_param(plain_name, plain_value) or _is_opaque(value):
        return name + "=" + FILTERED
    return chunk


def _scrub_query(query: str) -> str:
    """Mask sensitive or opaque names, values and bare chunks of a query string."""
    return "&".join(_scrub_query_chunk(chunk) for chunk in query.split("&"))


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
    scheme, _, rest = match.group(0).partition("://")
    authority_match = _AUTHORITY.match(rest)
    authority = authority_match.group(0) if authority_match else ""
    tail = rest[len(authority) :]
    authority = authority.rpartition("@")[2]  # drop user:password@
    if not _HOST.fullmatch(authority):
        # `user:pa/ss@host`: the `@` comes after a `/`; drop up to it.
        before_query = re.split(r"[?#]", rest, maxsplit=1)[0]
        if "@" in before_query:
            rest = rest[before_query.rindex("@") + 1 :]
            authority_match = _AUTHORITY.match(rest)
            authority = authority_match.group(0) if authority_match else ""
            tail = rest[len(authority) :]
        if not _HOST.fullmatch(authority):
            authority = FILTERED
    tail, hash_, fragment = tail.partition("#")
    path, question, query = tail.partition("?")
    url = scheme + "://" + authority + _scrub_path(path)
    if question:
        url += "?" + _scrub_query(query)
    if hash_ and fragment:
        url += "#" + FILTERED  # fragments carry OAuth / magic-link tokens
    return url


def scrub_text(text: Any) -> Any:
    """Mask secrets in free text (URLs, query strings, span names, messages).

    URL userinfo, `name=value` pairs with a sensitive name or an opaque value,
    URL fragments, and path segments after a secret-bearing route or that look
    like a token.
    """
    if not isinstance(text, str):
        return text
    if "://" in text:
        text = _URL.sub(_scrub_url, text)
    if "/" in text:
        text = _PATH.sub(lambda m: _scrub_path(m.group(0)), text)
    if "=" in text:
        text = _PAIR.sub(_scrub_pair, text)
    return text


def _scrub(value: Any, depth: int = 0) -> Any:
    """Blank sensitive keys and mask secrets in strings, recursively.

    Dicts and lists are edited in place; the scrubbed value is returned (tuples
    and sets come back as lists, bytes and other objects as scrubbed text).
    Fails closed: too deep means `[Filtered]`.
    """
    if depth > _MAX_DEPTH:
        return FILTERED
    if isinstance(value, dict):
        for key in list(value):
            if is_sensitive_key(key):
                value[key] = FILTERED
            else:
                value[key] = _scrub(value[key], depth + 1)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            value[i] = _scrub(item, depth + 1)
    elif isinstance(value, tuple | set | frozenset):
        return [_scrub(item, depth + 1) for item in value]
    elif isinstance(value, str):
        return scrub_text(value)
    elif isinstance(value, bytes | bytearray):
        return scrub_text(bytes(value).decode("utf-8", "replace"))
    elif value is None or isinstance(value, bool | int | float):
        return value
    elif hasattr(value, "value") and hasattr(value, "metadata"):
        value.value = _scrub(value.value, depth + 1)  # the SDK's AnnotatedValue
    else:
        # Anything else would be repr()'d by the SDK: scrub that text instead.
        return scrub_text(repr(value))
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
    exception and thread values, breadcrumb data, every stack frame's local
    variables and any embedded span.
    Written so that it cannot raise: in sentry-sdk 3.0 an exception in an event
    callback drops the event.
    """
    if not isinstance(event, dict):
        return event
    request = event.get("request")
    if isinstance(request, dict):
        for part in ("headers", "cookies", "data", "env", "url"):
            if part in request:
                request[part] = _scrub(request[part])
        if "query_string" in request:
            query = request["query_string"]
            request["query_string"] = (
                _scrub_query(query) if isinstance(query, str) else _scrub(query)
            )
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
    for container in ("exception", "threads"):
        values = event.get(container)
        values = values.get("values") if isinstance(values, dict) else None
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict) and "value" in value:
                    value["value"] = scrub_text(value["value"])
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
