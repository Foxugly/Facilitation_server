"""Le scenario se reordonne et s'elague (tache 2, design 2026-09-12 5d).

La file de rounds a desormais un ordre explicite (`Round.sequence`, tache 1) :
ce module teste les deux gestes qui en font un scenario composable en amont --
`reorder_rounds` et `remove_round` -- plus le cas de rejeu manque releve a la
relecture de la tache 1 (voir le dernier test).
"""
import pytest

from realtime import services
from realtime.services import RoomError
from realtime.tests.helpers import cast_first_item
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Round, RoundState, Room
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


def _three_rounds(room, fac):
    """A, B, C dans cet ordre -- A devient le round courant (premier round de
    la salle, regle deja en place de `add_scenario_item`)."""
    a_id = services.add_scenario_item(room, fac, "A")
    b_id = services.add_scenario_item(room, fac, "B")
    c_id = services.add_scenario_item(room, fac, "C")
    return a_id, b_id, c_id


def _act(room, fac, voter, card):
    """Joue le round courant jusqu'au bout : ouvre, vote, revele, acte."""
    services.open_vote(room, fac)
    cast_first_item(room, voter, card)
    services.reveal(room, fac)
    return services.act_result(room, fac, card)


@pytest.mark.django_db
def test_reorder_rounds_renumbers_contiguously_and_agenda_follows(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    a_id, b_id, c_id = _three_rounds(room, fac)

    out = services.reorder_rounds(room, fac, [c_id, a_id, b_id])

    assert [entry["id"] for entry in out] == [c_id, a_id, b_id]
    assert [Round.objects.get(id=rid).sequence for rid in (c_id, a_id, b_id)] == [1, 2, 3]
    agenda = services.build_agenda(room)
    assert [entry["id"] for entry in agenda] == [c_id, a_id, b_id]


@pytest.mark.django_db
def test_reorder_rounds_rejects_a_set_that_does_not_match(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    a_id, b_id, _c_id = _three_rounds(room, fac)

    with pytest.raises(RoomError) as exc:
        services.reorder_rounds(room, fac, [a_id, b_id])  # C manque

    assert exc.value.rejected_type == "round.reorder"


@pytest.mark.django_db
def test_reorder_rounds_rejects_duplicate_ids(room_with_facilitator):
    """Salle a UN SEUL round : set([a, a]) == {a} == known, donc seule la
    comparaison de longueur peut rejeter ce doublon -- meme piege que
    `reorder_items` (`test_reorder_items_rejects_duplicate_ids`)."""
    room, fac, _ = room_with_facilitator
    a_id = services.add_scenario_item(room, fac, "A")

    with pytest.raises(RoomError) as exc:
        services.reorder_rounds(room, fac, [a_id, a_id])

    assert exc.value.rejected_type == "round.reorder"


@pytest.mark.django_db
def test_removing_a_prepared_round_disappears_from_the_agenda(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    a_id, b_id, c_id = _three_rounds(room, fac)

    removed = services.remove_round(room, fac, b_id)

    assert removed == b_id
    agenda = services.build_agenda(room)
    assert [entry["id"] for entry in agenda] == [a_id, c_id]
    # La renumerotation ferme le trou : la sequence reste contigue 1..N.
    assert [Round.objects.get(id=rid).sequence for rid in (a_id, c_id)] == [1, 2]


@pytest.mark.django_db
def test_removing_a_round_that_carries_a_result_is_refused(room_with_facilitator):
    """L'historique ne se reecrit pas -- meme garde que pour un item deja acte.
    B est acte puis on bascule le round courant sur C : B n'est plus courant,
    pour isoler cette garde de celle sur le round courant (test suivant)."""
    room, fac, voter = room_with_facilitator
    a_id, b_id, c_id = _three_rounds(room, fac)
    services.select_round(room, fac, b_id)
    _act(room, fac, voter, "4")
    services.select_round(room, fac, c_id)

    with pytest.raises(RoomError) as exc:
        services.remove_round(room, fac, b_id)

    assert exc.value.rejected_type == "round.remove"
    assert Round.objects.filter(id=b_id).exists()


@pytest.mark.django_db
def test_removing_the_current_round_is_refused(room_with_facilitator):
    """Choix arrete (design, voir le commentaire de `remove_round`) : on refuse
    plutot que de designer un autre round courant a la place du facilitateur.
    Le round courant reste en place, intact."""
    room, fac, _voter = room_with_facilitator
    a_id, _b_id, _c_id = _three_rounds(room, fac)
    room.refresh_from_db()
    assert room.current_round_id == a_id

    with pytest.raises(RoomError) as exc:
        services.remove_round(room, fac, a_id)

    assert exc.value.rejected_type == "round.remove"
    room.refresh_from_db()
    assert room.current_round_id == a_id
    assert Round.objects.filter(id=a_id).exists()


@pytest.mark.django_db
def test_a_voter_is_refused_both_scenario_intentions(room_with_facilitator):
    room, fac, voter = room_with_facilitator
    a_id, b_id, c_id = _three_rounds(room, fac)

    with pytest.raises(RoomError) as exc:
        services.reorder_rounds(room, voter, [c_id, b_id, a_id])
    assert exc.value.rejected_type == "round.reorder"

    with pytest.raises(RoomError) as exc:
        services.remove_round(room, voter, b_id)
    assert exc.value.rejected_type == "round.remove"

    # Rien n'a bouge : les deux refus sont bien survenus avant toute ecriture.
    agenda = services.build_agenda(room)
    assert [entry["id"] for entry in agenda] == [a_id, b_id, c_id]


@pytest.mark.django_db
def test_replaying_a_round_that_is_not_last_appends_at_the_end(room_with_facilitator):
    """Relecture de la tache 1 : le seul test de rejeu existant
    (`test_round_replay.py`) n'a qu'UN round au moment du rejeu -- la copie
    parait forcement « en fin de file » puisqu'il n'y a rien d'autre. Ce test
    place A, B, C (B et C deja dans la file AVANT que A soit rejoue) pour que
    « inserer juste apres la source » et « ajouter en fin de file » produisent
    deux agendas differents -- et prouver lequel des deux le code choisit."""
    room, fac, voter = room_with_facilitator
    a_id, b_id, c_id = _three_rounds(room, fac)
    _act(room, fac, voter, "4")  # A est le round courant : on l'acte en premier.

    out = services.select_round(room, fac, a_id)  # A est ACTED : rejeu.
    replay_id = out["roundId"]

    assert replay_id not in (a_id, b_id, c_id)
    agenda = services.build_agenda(room)
    # La copie se place APRES C, pas juste apres A (qui l'aurait mise en 2e
    # position, devant B et C).
    assert [entry["id"] for entry in agenda] == [a_id, b_id, c_id, replay_id]
    room.refresh_from_db()
    assert room.current_round_id == replay_id
