"""Le domaine manipule des items de round (design 2026-09-11 §3, §5).

Tests synchrones : `services` est volontairement pauvre en framework, donc
testable sans socket.
"""
import pytest

from realtime import services
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, RoundState
from rooms.snapshot import build_deck_snapshot


@pytest.fixture
def room_with_facilitator(standard_deck):
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=standard_deck.vote_type,
        deck_snapshot=build_deck_snapshot(standard_deck),
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(
        room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR
    )
    voter = Participant.objects.create(
        room=room, token=generate_token(), display_name="Alex", role=Role.VOTER
    )
    return room, fac, voter


@pytest.mark.django_db
def test_add_item_puts_n_items_on_the_same_round(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    services.add_item(room, fac, "Embauche ?")

    room.refresh_from_db()
    assert [i["text"] for i in services.items_payload(room.current_round)] == [
        "Budget ?",
        "Embauche ?",
    ]
    assert [i["sequence"] for i in services.items_payload(room.current_round)] == [1, 2]


@pytest.mark.django_db
def test_a_voter_may_not_add_an_item_to_a_poker_round(room_with_facilitator):
    """En 5a la creation d'items reste facilitateur seul. Le registre ouvrira la
    porte aux participants en 5c (`items_authored_by`)."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")

    with pytest.raises(RoomError) as exc:
        services.add_item(room, voter, "Mon post-it")

    assert exc.value.rejected_type == "item.add"


@pytest.mark.django_db
def test_update_and_remove_item(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    second = services.add_item(room, fac, "Embauche ?")

    services.update_item(room, fac, second["id"], "Embauche 2027 ?")
    room.refresh_from_db()
    assert services.items_payload(room.current_round)[1]["text"] == "Embauche 2027 ?"

    services.remove_item(room, fac, second["id"])
    room.refresh_from_db()
    assert [i["text"] for i in services.items_payload(room.current_round)] == ["Budget ?"]


@pytest.mark.django_db
def test_reorder_items_renumbers_the_sequence(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "A")
    b = services.add_item(room, fac, "B")
    room.refresh_from_db()
    a = services.items_payload(room.current_round)[0]

    out = services.reorder_items(room, fac, [b["id"], a["id"]])

    assert [i["text"] for i in out] == ["B", "A"]
    assert [i["sequence"] for i in out] == [1, 2]


@pytest.mark.django_db
def test_agenda_lists_rounds_and_select_round_resets_to_idle(room_with_facilitator):
    """L'agenda designe desormais des ROUNDS. Le front renvoie l'id qu'il a recu,
    donc l'alias `subject.select` continue de fonctionner sans le savoir."""
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    services.add_scenario_item(room, fac, "Embauche ?")

    agenda = services.build_agenda(room)
    assert [e["text"] for e in agenda] == ["Budget ?", "Embauche ?"]

    out = services.select_round(room, fac, agenda[1]["id"])
    room.refresh_from_db()
    assert out["text"] == "Embauche ?"
    assert room.current_round_id == agenda[1]["id"]
    assert room.current_round.state == RoundState.IDLE


@pytest.mark.django_db
def test_state_sync_carries_items_and_the_legacy_subject(room_with_facilitator):
    """Les deux formes cohabitent le temps que le front bascule (design §5)."""
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")

    state = services.build_state_sync(fac)

    assert state["subject"] == "Budget ?"
    assert [i["text"] for i in state["items"]] == ["Budget ?"]
    assert state["round"]["id"] == Room.objects.get(pk=room.pk).current_round_id
