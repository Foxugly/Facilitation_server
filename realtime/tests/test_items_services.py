"""Le domaine manipule des items de round (design 2026-09-11 §3, §5).

Tests synchrones : `services` est volontairement pauvre en framework, donc
testable sans socket.
"""
import pytest

from realtime import services
from realtime.services import RoomError
from realtime.tests.helpers import cast_first_item
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Role, Room, RoundState
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
def test_reorder_items_rejects_duplicate_ids(room_with_facilitator):
    """Round a UN SEUL item : set([a, a]) == {a} == known, donc seule la
    comparaison de longueur peut rejeter ce doublon. Avec un second item present
    (b non repris dans item_ids), le controle d'ensemble suffirait deja a lui
    seul et le test ne demontrerait rien sur la garde de longueur."""
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "A")
    room.refresh_from_db()
    a = services.items_payload(room.current_round)[0]

    with pytest.raises(RoomError) as exc:
        services.reorder_items(room, fac, [a["id"], a["id"]])

    assert exc.value.rejected_type == "item.reorder"


@pytest.mark.django_db
def test_agenda_lists_rounds_and_select_round_resets_to_idle(room_with_facilitator):
    """L'agenda designe desormais des ROUNDS ; `select_round` (ex-`select_subject`)
    en reprend un et le remet a idle."""
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


@pytest.mark.django_db
def test_cannot_remove_an_item_from_a_round_in_flight(room_with_facilitator):
    """C1 : retirer le dernier item d'un round revele laissait `act_result` sans
    item ou accrocher son `Result` (colonne NOT NULL) — l'IntegrityError qui
    s'ensuivait n'est pas un RoomError et fermait la socket du facilitateur."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    item_id = services.items_payload(services.current_round(room))[0]["id"]
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)

    with pytest.raises(RoomError) as exc:
        services.remove_item(room, fac, item_id)

    assert exc.value.rejected_type == "item.remove"
    # Le round est reste actable : la garde protege, elle ne bloque pas.
    assert services.act_result(room, fac, "4") == "4"


@pytest.mark.django_db
def test_act_result_refuses_a_round_left_without_item(room_with_facilitator):
    """Defense en profondeur derriere la garde ci-dessus : si un item disparaissait
    par un autre chemin (cascade, script d'admin), `result.act` doit rendre un
    RoomError — le consumer ne rattrape que celui-la."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)
    Item.objects.filter(round=services.current_round(room)).delete()

    with pytest.raises(RoomError) as exc:
        services.act_result(room, fac, "4")

    assert exc.value.rejected_type == "result.act"


@pytest.mark.django_db
def test_cannot_rewrite_an_item_already_decided(room_with_facilitator):
    """I2 : `history/api_views.py` affiche `Result.item.text`. Reformuler un item
    deja acte changerait retroactivement le rapport envoye aux managers — meme
    garde que `remove_item`, qui refusait deja."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    item_id = services.items_payload(services.current_round(room))[0]["id"]
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)
    services.act_result(room, fac, "4")

    with pytest.raises(RoomError) as exc:
        services.update_item(room, fac, item_id, "Budget 2027 ?")

    assert exc.value.rejected_type == "item.update"
    assert Item.objects.get(id=item_id).text == "Budget ?"
