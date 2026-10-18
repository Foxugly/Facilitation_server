"""Per-connection WebSocket limits (audit 2026-10-18, I5 + M7).

Each intention writes to the database and fans out to the room: a socket that
floods is cut off. Oversized frames are refused before JSON decoding.
"""
import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from realtime import consumers
from realtime.routing import websocket_urlpatterns
from realtime.tests.test_consumer import _make_room


async def _open(code):
    comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/rooms/{code}/")
    connected, _ = await comm.connect()
    assert connected
    return comm


@pytest.mark.django_db(transaction=True)
async def test_flooding_socket_is_closed(monkeypatch):
    monkeypatch.setattr(consumers, "WS_RATE_LIMIT", (5, 10.0))
    code, _fac, _voter = await database_sync_to_async(_make_room)()
    comm = await _open(code)
    for _ in range(5):
        await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
        assert (await comm.receive_json_from())["type"] == "pong"
    await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
    msg = await comm.receive_json_from()
    assert msg["type"] == "error" and msg["payload"]["code"] == "rate.limited"
    out = await comm.receive_output()
    assert out["type"] == "websocket.close" and out["code"] == 4008


@pytest.mark.django_db(transaction=True)
async def test_oversized_frame_is_refused(monkeypatch):
    monkeypatch.setattr(consumers, "WS_MAX_MESSAGE_BYTES", 256)
    code, _fac, _voter = await database_sync_to_async(_make_room)()
    comm = await _open(code)
    await comm.send_json_to({"v": 1, "type": "ping", "payload": {"pad": "x" * 500}})
    msg = await comm.receive_json_from()
    assert msg["type"] == "error" and msg["payload"]["code"] == "payload.too_large"
    out = await comm.receive_output()
    assert out["type"] == "websocket.close" and out["code"] == 4009
