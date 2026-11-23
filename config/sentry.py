"""What the Sentry SDK may collect, and how it is initialised (sentry-sdk >= 3.0).

``data_collection`` (3.0 removed ``send_default_pii``) collects no more than
``send_default_pii=False`` did: no user (id, e-mail, IP), no cookies, no request
or response bodies, no Celery task arguments, no SQL parameters, no GenAI/MCP
content. Headers, query strings and local variables go through the SDK's
sensitive-key denylist, plus the proxy/IP header terms the 3.0 migration guide
gives to match ``send_default_pii=False``.

3.0 also removed the built-in EventScrubber and ``before_send_transaction``:
``config.sentry_scrub`` replaces them (``before_send`` / ``before_send_span``).
"""

from __future__ import annotations

import logging
from typing import Any

from config.sentry_scrub import scrub_event, scrub_span

_PII_TERMS = ["forwarded", "-ip", "remote-", "via", "-user"]

DATA_COLLECTION: dict[str, Any] = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {"request": {"mode": "denylist", "terms": _PII_TERMS}},
    "http_bodies": [],
    "url_query_params": {"mode": "denylist", "terms": _PII_TERMS},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "mcp": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": {"mode": "denylist"},
    "frame_context_lines": 5,
}


def sentry_init_options() -> dict[str, Any]:
    """``sentry_sdk.init`` options other than DSN, environment, release and sampling.

    Kept here to be testable: settings only initialise Sentry in production.
    """
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    return {
        # No PII, cookies or request bodies: see DATA_COLLECTION.
        "data_collection": DATA_COLLECTION,
        # 3.0 turned this on: a plain message would carry the stack and its locals.
        "attach_stacktrace": False,
        "integrations": [
            # 3.0 traces cache calls by default: keep 2.x's span volume.
            DjangoIntegration(cache_spans=False),
            # 3.0 no longer enables logging by default and its `event_level` is
            # None: without this, `logger.error()` would stop creating issues.
            # `level=None`: no Sentry Logs, which 2.x did not send either.
            LoggingIntegration(
                level=None,
                event_level=logging.ERROR,
                breadcrumb_level=logging.INFO,
            ),
        ],
        "before_send": scrub_event,
        "before_send_span": scrub_span,
    }
