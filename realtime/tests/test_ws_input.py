"""Malformed WebSocket input gets an error frame, never a dead socket (audit M1).

A non-object payload, a non-string text or an over-long item used to raise
inside the consumer: the socket closed and the exception reached Sentry.
"""
import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from realtime.routing import websocket_urlpatterns
from realtime.tests.test_consumer import _make_room
from rooms.models import Item


async def _joined(code, token):
    comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/rooms/{code}/")
    connected, _ = await comm.connect()
    assert connected
    await comm.send_json_to({"v": 1, "type": "session.join", "payload": {"participantToken": token}})
    assert (await comm.receive_json_from())["type"] == "state.sync"
    while not await comm.receive_nothing(timeout=0.1):
        await comm.receive_json_from()
    return comm


async def _expect_error(comm, code):
    msg = await comm.receive_json_from()
    while msg["type"] != "error":
        msg = await comm.receive_json_from()
    assert msg["payload"]["code"] == code, msg
    # The socket is still usable afterwards.
    await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
    while (await comm.receive_json_from())["type"] != "pong":
        pass


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "message",
    [
        {"v": 1, "type": "item.add", "payload": "not an object"},
        {"v": 1, "type": "item.add", "payload": [1, 2]},
        {"v": 1, "type": "item.add", "payload": {"text": 42}},
        {"v": 1, "type": "item.add", "payload": {"text": "x" * 301}},
        {"v": 1, "type": "item.update", "payload": {"itemId": 1, "text": {"a": 1}}},
        {"v": 1, "type": "item.update", "payload": {"itemId": 1, "text": "x" * 301}},
        {"v": 1, "type": "round.add", "payload": {"text": ["x"]}},
        {"v": 1, "type": "round.add", "payload": {"text": "x" * 301}},
        {"v": 1, "type": "round.prepare", "payload": {"subjectText": ["x"]}},
        {"v": 1, "type": "round.prepare", "payload": {"subjectText": "x" * 301}},
    ],
)
async def test_bad_payloads_get_payload_invalid(message):
    code, fac, _voter = await database_sync_to_async(_make_room)()
    comm = await _joined(code, fac)
    await comm.send_json_to(message)
    await _expect_error(comm, "payload.invalid")
    assert not await database_sync_to_async(Item.objects.filter(round__room__code=code, text__startswith="xxx").exists)()
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_non_object_envelope_and_bad_json_are_refused():
    code, fac, _voter = await database_sync_to_async(_make_room)()
    comm = await _joined(code, fac)
    await comm.send_json_to([1, 2, 3])
    await _expect_error(comm, "payload.invalid")
    await comm.send_to(text_data="{not json")
    await _expect_error(comm, "payload.invalid")
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_unexpected_server_error_is_contained(monkeypatch):
    from realtime import services

    def boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(services, "add_item", boom)
    code, fac, _voter = await database_sync_to_async(_make_room)()
    comm = await _joined(code, fac)
    await comm.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "ok"}})
    await _expect_error(comm, "server.error")
    await comm.disconnect()
