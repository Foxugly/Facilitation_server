"""What the Sentry SDK may collect (sentry-sdk >= 2.76).

``data_collection`` replaces the deprecated ``send_default_pii=False`` and
collects no more than before: no user (id, e-mail, IP), no cookies, no request
or response bodies, no Celery task arguments, no SQL parameters. Headers, query
strings and local variables go through the SDK's sensitive-key denylist, plus
the proxy/IP header terms the SDK itself uses when it maps
``send_default_pii=False``.

Passing ``data_collection`` switches off the SDK's built-in EventScrubber, so
``scrub_event`` re-applies it (extra, breadcrumbs, spans, frame variables) from
``before_send`` / ``before_send_transaction``.
"""

from __future__ import annotations

from typing import Any

from sentry_sdk.scrubber import EventScrubber

_PII_TERMS = ["forwarded", "-ip", "remote-", "via", "-user"]

DATA_COLLECTION: dict[str, Any] = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {"request": {"mode": "denylist", "terms": _PII_TERMS}},
    "http_bodies": [],
    "url_query_params": {"mode": "denylist", "terms": _PII_TERMS},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": {"mode": "denylist"},
    "frame_context_lines": 5,
}

_SCRUBBER = EventScrubber(send_default_pii=False)


def scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """``before_send_transaction`` (and first step of ``before_send``)."""
    _SCRUBBER.scrub_event(event)
    return event
