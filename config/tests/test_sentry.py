"""sentry-sdk 3.0: the init options, scrubbing and logging, through a real client.

The client sends to an in-memory transport, so what is asserted is what would
actually leave the box.
"""

import json
import logging
import warnings
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from config.sentry import DATA_COLLECTION, sentry_init_options
from config.sentry_scrub import FILTERED, scrub_event, scrub_span, scrub_text

SECRET = "s3cr3t-value-never-sent"


class _Capture(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def items(self, kind):
        return [
            item
            for envelope in self.envelopes
            for item in envelope.items
            if item.headers.get("type") == kind
        ]

    def dump(self):
        return "".join(
            item.payload.get_bytes().decode("utf-8", "replace")
            for envelope in self.envelopes
            for item in envelope.items
        )


@pytest.fixture
def sentry(request):
    """`sentry_sdk.init` with the production options, into memory."""
    transport = _Capture()
    traces = getattr(request, "param", 0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no deprecated option, no unknown key
        sentry_sdk.init(
            dsn="https://public@sentry.example.invalid/1",
            transport=transport,
            traces_sample_rate=traces,
            **sentry_init_options(),
        )
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def _events(transport):
    sentry_sdk.flush()
    return [json.loads(item.payload.get_bytes()) for item in transport.items("event")]


def test_init_takes_the_options_and_collects_no_more_than_2x(sentry):
    options = sentry_sdk.get_client().options
    rules = options["data_collection"]
    assert rules["user_info"] is False
    assert rules["cookies"]["mode"] == "off"
    assert rules["http_bodies"] == []
    assert rules["queues"] is False
    assert rules["database_query_data"] is False
    assert rules["gen_ai"] == {"inputs": False, "outputs": False}
    assert rules["mcp"] == {"inputs": False, "outputs": False}
    assert rules["stack_frame_variables"]["mode"] == "denylist"
    assert options["attach_stacktrace"] is False
    assert options["before_send_span"] is scrub_span


def test_before_send_masks_request_extra_and_local_variables(sentry):
    def view():
        password = SECRET  # noqa: F841 (a local variable Sentry would ship)
        api_token = SECRET  # noqa: F841
        raise ValueError("boom")

    with sentry_sdk.new_scope() as scope:
        scope.set_extra("token", SECRET)
        scope.set_extra("page", 3)
        try:
            view()
        except ValueError:
            sentry_sdk.capture_exception()

    (event,) = _events(sentry)
    assert event["extra"]["token"] == FILTERED
    assert event["extra"]["page"] == 3
    frame = event["exception"]["values"][-1]["stacktrace"]["frames"][-1]
    assert frame["function"] == "view"
    assert SECRET not in json.dumps(event)


def test_scrub_event_masks_request_data_headers_and_user():
    event = scrub_event(
        {
            "request": {
                "data": {"password": SECRET, "nested": {"refresh_token": SECRET}, "n": 1},
                "headers": {"Authorization": SECRET, "X-Real-Ip": SECRET, "Accept": "*/*"},
                "cookies": {"sessionid": SECRET},
                "query_string": "token=" + SECRET + "&page=2",
            },
            "extra": {"secret": SECRET},
            "user": {"id": "7", "ip_address": SECRET},
            "breadcrumbs": {"values": [{"data": {"api_key": SECRET, "n": 1}}]},
            "exception": {
                "values": [{"stacktrace": {"frames": [{"vars": {"passwd": SECRET, "i": 1}}]}}]
            },
        },
        {},
    )
    assert SECRET not in json.dumps(event)
    assert event["request"]["data"]["n"] == 1
    assert event["request"]["headers"]["Accept"] == "*/*"
    assert event["request"]["query_string"] == "token=[Filtered]&page=2"
    assert "ip_address" not in event["user"]
    assert event["breadcrumbs"]["values"][0]["data"]["n"] == 1
    assert event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["i"] == 1


def test_scrub_event_survives_odd_shapes():
    """In 3.0 an exception in an event callback drops the event."""
    for event in ({}, {"request": "x"}, {"exception": {"values": [None]}}, {"user": None}):
        assert scrub_event(event, {}) is event


def test_scrub_span_masks_name_and_attributes():
    span = scrub_span(
        {
            "name": "GET /reset?token=" + SECRET,
            "attributes": {
                "http.request.header.authorization": [SECRET],
                "url.full": "https://x.invalid/?api_key=" + SECRET + "&page=2",
                "http.request.method": "GET",
            },
        },
        {},
    )
    assert SECRET not in json.dumps(span)
    assert span["name"] == "GET /reset?token=[Filtered]"
    assert span["attributes"]["url.full"] == "https://x.invalid/?api_key=[Filtered]&page=2"
    assert span["attributes"]["http.request.method"] == "GET"


@pytest.mark.parametrize("sentry", [1.0], indirect=True)
def test_before_send_span_masks_what_is_sent(sentry):
    with sentry_sdk.start_span(
        name="GET /reset?token=" + SECRET,
        attributes={"session_key": SECRET, "page": 2},
    ):
        pass
    sentry_sdk.flush()
    sent = sentry.dump()
    assert "/reset?token=[Filtered]" in sent
    assert SECRET not in sent


def test_logger_error_still_creates_an_event(sentry):
    logging.getLogger("rooms").error("payment failed for invoice %s", 42)
    events = _events(sentry)
    assert [e["logentry"]["message"] for e in events] == ["payment failed for invoice %s"]
    assert "stacktrace" not in json.dumps(events)  # attach_stacktrace stays off


# --- Security review follow-up: contexts, embedded spans, URLs, response headers.

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjF9.c2lnbmF0dXJlMTIz"


def test_scrub_event_scrubs_contexts_recursively() -> None:
    event = scrub_event(
        {
            "contexts": {
                "response": {"headers": {"Set-Cookie": SECRET, "X-Ok": "y"}},
                "trace": {"data": {"url.full": "https://h.invalid/?token=" + SECRET}},
            }
        },
        {},
    )
    assert SECRET not in json.dumps(event)
    assert event["contexts"]["response"]["headers"]["X-Ok"] == "y"


def test_scrub_event_scrubs_embedded_spans() -> None:
    event = scrub_event(
        {
            "spans": [
                {
                    "description": "GET /reset-password/" + SECRET + "/",
                    "data": {"authorization": SECRET, "http.query": "token=" + SECRET},
                }
            ]
        },
        {},
    )
    assert SECRET not in json.dumps(event)
    assert event["spans"][0]["description"] == "GET /reset-password/[Filtered]/"


def test_scrub_text_drops_url_userinfo() -> None:
    text = scrub_text("fetch https://bob:" + SECRET + "@h.invalid/a?page=2")
    assert text == "fetch https://h.invalid/a?page=2"


def test_scrub_text_masks_secret_path_segments() -> None:
    assert scrub_text("GET /auth/magic-link/abc123/") == "GET /auth/magic-link/[Filtered]/"
    assert scrub_text("GET /invitation/xyz/accept") == "GET /invitation/[Filtered]/accept"
    assert scrub_text("GET /files/" + JWT + "/") == "GET /files/[Filtered]/"
    # Route templates and ordinary paths stay readable.
    assert scrub_text("GET /api/reset/<token>/") == "GET /api/reset/<token>/"
    assert scrub_text("GET /api/v1/invoices/42/") == "GET /api/v1/invoices/42/"


def test_scrub_span_masks_url_paths_in_attributes() -> None:
    span = scrub_span(
        {"name": "GET", "attributes": {"url.full": "https://u:p@h.invalid/activate/" + SECRET}},
        {},
    )
    assert span["attributes"]["url.full"] == "https://h.invalid/activate/[Filtered]"


def test_response_headers_are_never_collected() -> None:
    assert DATA_COLLECTION["http_headers"]["response"] == {"mode": "off"}
    client = sentry_sdk.Client(
        dsn=None, default_integrations=False, data_collection=DATA_COLLECTION
    )
    # 3.0.0 has no response-header setting (it collects none); if a release adds
    # one, it must resolve to "off".
    headers = client.options["data_collection"]["http_headers"]
    assert headers.get("response", {"mode": "off"})["mode"] == "off"


# --- Second security review: exception values, query/fragment, userinfo, fail closed.


def test_exception_and_thread_values_are_scrubbed() -> None:
    event = scrub_event(
        {
            "exception": {
                "values": [{"value": "GET https://h.invalid/x?token=" + SECRET}]
            },
            "threads": {"values": [{"value": "retry with api_key=" + SECRET}]},
        },
        {},
    )
    assert SECRET not in json.dumps(event)


def test_query_and_fragment_values_are_masked() -> None:
    url = "https://h.invalid/cb?page=2&ticket=t&sig=AbCdEfGhIjKlMnOp#access_token=x"
    assert scrub_text(url) == (
        "https://h.invalid/cb?page=2&ticket=[Filtered]&sig=[Filtered]#[Filtered]"
    )
    assert scrub_text("next=/a&code=0123456789abcdef") == "next=/a&code=[Filtered]"
    event = scrub_event({"request": {"query_string": "page=2&k=" + JWT}}, {})
    assert event["request"]["query_string"] == "page=2&k=[Filtered]"


def test_userinfo_is_stripped_robustly() -> None:
    assert scrub_text("https://u:pa@ss@h.invalid/") == "https://h.invalid/"
    assert scrub_text("https://u:p/w@h.invalid/x") == "https://h.invalid/x"
    assert scrub_text("https://user:" + SECRET + "/x") == "https://[Filtered]/x"
    assert scrub_text("https://h.invalid:8443/a") == "https://h.invalid:8443/a"
    assert scrub_text("http://[::1]:8000/a") == "http://[::1]:8000/a"


def test_scrubbing_fails_closed() -> None:
    deep: Any = SECRET
    for _ in range(30):
        deep = [deep]
    event = scrub_event(
        {"extra": {"t": ("a?token=" + SECRET,), "s": {"x?password=" + SECRET}, "d": deep}},
        {},
    )
    assert SECRET not in json.dumps(event)
