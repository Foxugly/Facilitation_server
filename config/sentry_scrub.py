"""Repo-local scrubbing of what goes to Sentry (sentry-sdk >= 3.0).

sentry-sdk 3.0 removed the built-in ``EventScrubber`` (and ``before_send_transaction``).
``scrub_event`` redoes what it did in ``before_send``; ``scrub_span`` applies the same
denylist to spans in ``before_send_span``. Self-contained on purpose: no SDK internals.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

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
_MAX_DEPTH = 20


def is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return normalized in _DENYLIST or any(part in normalized for part in _DENY_PARTS)


def scrub_text(text: Any) -> Any:
    """Blank the value of every sensitive `name=value` pair in a string."""
    if not isinstance(text, str) or "=" not in text:
        return text
    return _SENSITIVE_PAIR.sub(lambda m: m.group(1) + FILTERED, text)


def _scrub(value: Any, depth: int = 0) -> None:
    """Blank sensitive keys of every dict found in `value`, in place."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, dict):
        for key in list(value):
            if is_sensitive_key(key):
                value[key] = FILTERED
            else:
                _scrub(value[key], depth + 1)
    elif isinstance(value, list):
        for item in value:
            _scrub(item, depth + 1)


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

    Request headers, cookies, body and query string, `extra`, the user (whose IP
    is dropped), breadcrumb data and every stack frame's local variables.
    Written so that it cannot raise: in sentry-sdk 3.0 an exception in an event
    callback drops the event.
    """
    if not isinstance(event, dict):
        return event
    request = event.get("request")
    if isinstance(request, dict):
        for part in ("headers", "cookies", "data", "env"):
            _scrub(request.get(part))
        for part in ("query_string", "url"):
            if isinstance(request.get(part), str):
                request[part] = scrub_text(request[part])
    _scrub(event.get("extra"))
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
        _scrub(user)
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else None
    if isinstance(values, list):
        for crumb in values:
            if isinstance(crumb, dict):
                _scrub(crumb.get("data"))
    for frame in _frames(event):
        _scrub(frame.get("vars"))
    return event


def scrub_span(span: Any, hint: Any = None) -> Any:
    """`before_send_span`: the same denylist on the span name and attributes.

    A span cannot be dropped here (sentry-sdk 3.0), only rewritten.
    """
    if not isinstance(span, dict):
        return span
    if isinstance(span.get("name"), str):
        span["name"] = scrub_text(span["name"])
    attributes = span.get("attributes")
    if isinstance(attributes, dict):
        for key, value in list(attributes.items()):
            if is_sensitive_key(key) or is_sensitive_key(key.rsplit(".", 1)[-1]):
                attributes[key] = FILTERED
            elif isinstance(value, str):
                attributes[key] = scrub_text(value)
            elif isinstance(value, list):
                attributes[key] = [scrub_text(v) for v in value]
    return span
