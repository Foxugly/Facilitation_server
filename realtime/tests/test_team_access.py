"""Team rooms: realtime access follows team membership (audit 2026-10-18, I1).

A member removed from the team, or whose account was deactivated, must lose the
room: ``session.join`` is refused, every later intention is refused, and a socket
that is already in the room stops receiving the room's broadcasts.
"""
import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.utils import timezone

from decks.seed import create_standard_deck
from realtime import consumers
from realtime.routing import websocket_urlpatterns
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room
from rooms.tasks import expire_stale_rooms
from teams.models import Team, TeamMembership, TeamRole
from rooms.snapshot import build_deck_snapshot

User = get_user_model()


def _team_room():
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    owner = User.objects.create_user(email=f"o-{code.lower()}@example.com", password="pw12345678")
    member = User.objects.create_user(email=f"m-{code.lower()}@example.com", password="pw12345678")
    team = Team.objects.create(name="Squad", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    TeamMembership.objects.create(team=team, user=member, role=TeamRole.MEMBER)
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), team=team)
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(
        room=room, token=generate_token(), display_name="O", role=Role.FACILITATOR, user=owner
    )
    voter = Participant.objects.create(
        room=room, token=generate_token(), display_name="M", role=Role.VOTER, user=member
    )
    return code, team, member, fac.token, voter.token


async def _open(code):
    comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/rooms/{code}/")
    connected, _ = await comm.connect()
    assert connected
    return comm


async def _join(comm, token):
    await comm.send_json_to({"v": 1, "type": "session.join", "payload": {"participantToken": token}})
    return await comm.receive_json_from()


def _remove_member(team, user):
    TeamMembership.objects.filter(team=team, user=user).delete()


def _deactivate(user):
    User.objects.filter(pk=user.pk).update(is_active=False)


@pytest.mark.django_db(transaction=True)
async def test_removed_member_cannot_join_team_room():
    code, team, member, _fac, voter = await database_sync_to_async(_team_room)()
    await database_sync_to_async(_remove_member)(team, member)
    comm = await _open(code)
    msg = await _join(comm, voter)
    assert msg["type"] == "error" and msg["payload"]["code"] == "token.unknown"
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_deactivated_member_cannot_join_team_room():
    code, _team, member, _fac, voter = await database_sync_to_async(_team_room)()
    await database_sync_to_async(_deactivate)(member)
    comm = await _open(code)
    msg = await _join(comm, voter)
    assert msg["type"] == "error" and msg["payload"]["code"] == "token.unknown"
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_member_removed_mid_session_loses_intentions_and_broadcasts(monkeypatch):
    # Recheck membership on every broadcast so the test needs no clock.
    monkeypatch.setattr(consumers, "MEMBERSHIP_RECHECK_SECONDS", 0)
    code, team, member, fac_token, voter_token = await database_sync_to_async(_team_room)()
    fac = await _open(code)
    assert (await _join(fac, fac_token))["type"] == "state.sync"
    voter = await _open(code)
    assert (await _join(voter, voter_token))["type"] == "state.sync"

    await database_sync_to_async(_remove_member)(team, member)

    # An intention from the removed member is refused...
    await voter.send_json_to({"v": 1, "type": "vote.cast", "payload": {"cardValue": "1"}})
    msg = await voter.receive_json_from()
    while msg["type"] != "error":
        msg = await voter.receive_json_from()
    assert msg["payload"]["code"] == "token.unknown"

    while not await voter.receive_nothing(timeout=0.2):
        leftover = await voter.receive_json_from()
        assert leftover["type"] == "error" and leftover["payload"]["code"] == "token.unknown"
    # ...and the room's later broadcasts no longer reach the socket.
    await fac.send_json_to({"v": 1, "type": "round.add", "payload": {"text": "Secret topic"}})
    assert await voter.receive_nothing(timeout=0.3)
    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_passive_socket_is_evicted_on_next_broadcast(monkeypatch):
    monkeypatch.setattr(consumers, "MEMBERSHIP_RECHECK_SECONDS", 0)
    code, team, member, fac_token, voter_token = await database_sync_to_async(_team_room)()
    fac = await _open(code)
    assert (await _join(fac, fac_token))["type"] == "state.sync"
    voter = await _open(code)
    assert (await _join(voter, voter_token))["type"] == "state.sync"
    # Drain the join chatter on the voter socket.
    while not await voter.receive_nothing(timeout=0.2):
        await voter.receive_json_from()

    await database_sync_to_async(_deactivate)(member)
    await fac.send_json_to({"v": 1, "type": "round.add", "payload": {"text": "Secret topic"}})

    msg = await voter.receive_json_from()
    assert msg["type"] == "error" and msg["payload"]["code"] == "token.unknown"
    assert await voter.receive_nothing(timeout=0.3)
    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db
def test_team_participant_without_user_is_refused():
    from realtime.services import resolve_participant

    code, _team, _member, _fac, voter = _team_room()
    Participant.objects.filter(token=voter).update(user=None)
    assert resolve_participant(code, voter) is None


@pytest.mark.django_db
def test_team_room_expires_after_long_inactivity(settings):
    settings.TEAM_ROOM_INACTIVITY_DAYS = 30
    code, *_ = _team_room()
    room = Room.objects.get(code=code)
    # The 8h window of anonymous rooms does not apply to a team room...
    Room.objects.filter(pk=room.pk).update(expires_at=timezone.now() - timezone.timedelta(hours=1))
    room.refresh_from_db()
    assert room.is_live is True
    # ...but a month without activity closes it.
    Room.objects.filter(pk=room.pk).update(last_activity_at=timezone.now() - timezone.timedelta(days=31))
    room.refresh_from_db()
    assert room.is_live is False
    expire_stale_rooms()
    room.refresh_from_db()
    assert room.is_expired is True
