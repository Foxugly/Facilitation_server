"""Volume guard on items (audit 2026-10-18, M2).

No activity lets participants author items yet, but add_item already accepts
them under items_authored_by == "participants". Each item.add re-broadcasts the
agenda and totals to the room, so participants get a per-round cap, and every
round a total cap.
"""
import pytest

from realtime import activities, services
from realtime.activities import ActivitySpec
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room
from rooms.snapshot import build_deck_snapshot


@pytest.fixture
def room(standard_deck, monkeypatch):
    monkeypatch.setitem(
        activities.ACTIVITY_REGISTRY, "delegation_v1", ActivitySpec(ordinal=True, items_authored_by="participants")
    )
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=standard_deck.vote_type,
        deck_snapshot=build_deck_snapshot(standard_deck),
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    other = Participant.objects.create(room=room, token=generate_token(), display_name="Kim", role=Role.VOTER)
    services.set_current_item(room, fac, "Ideas?")
    return room, fac, voter, other


@pytest.mark.django_db
def test_a_participant_is_capped_per_round(room, monkeypatch):
    monkeypatch.setattr(services, "ITEMS_PER_PARTICIPANT_PER_ROUND", 2)
    room, _fac, voter, other = room
    services.add_item(room, voter, "one")
    services.add_item(room, voter, "two")
    with pytest.raises(RoomError) as exc:
        services.add_item(room, voter, "three")
    assert exc.value.code == "limit.reached"
    # Another participant still has their own budget.
    services.add_item(room, other, "mine")


@pytest.mark.django_db
def test_a_round_is_capped_for_everyone(room, monkeypatch):
    monkeypatch.setattr(services, "ITEMS_PER_ROUND", 3)
    room, fac, voter, _other = room
    services.add_item(room, voter, "a")
    services.add_item(room, fac, "b")
    with pytest.raises(RoomError) as exc:
        services.add_item(room, fac, "c")
    assert exc.value.code == "limit.reached"


def test_default_caps():
    assert services.ITEMS_PER_PARTICIPANT_PER_ROUND == 20
    assert services.ITEMS_PER_ROUND == 200
