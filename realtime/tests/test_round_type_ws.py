"""round.configure sur le contrat WebSocket (design 5c, contrat SS8.3).

Etend round.prepare/deck.select : round.configure fige la config (et, en
option, le deck) d'un round encore idle, sans repasser par tout round.prepare
quand un seul des deux doit changer. `realtime/services.py::configure_round`
porte la logique de domaine (task 2) ; ces tests ne verifient que le cablage
consumer <-> contrat.
"""
import pytest
from channels.db import database_sync_to_async

from decks.models import Deck
from decks.seed import create_standard_deck
from realtime.tests.test_consumer import _drain_until, _join, _make_room
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room
from rooms.snapshot import build_deck_snapshot


def _second_deck(vote_type):
    """Un second deck jouable (une seule carte suffit a distinguer les snapshots)."""
    deck = Deck.objects.create(vote_type=vote_type, is_standard=False, card_back_image="decks/backs/b.webp")
    deck.set_current_language("en")
    deck.name = "Fibonacci"
    deck.save()
    deck.cards.create(value="13", slug="thirteen", order=1, background_image="decks/cards/13.webp")
    return deck


def _make_room_with_two_decks():
    """Meme forme que `_make_room`, mais avec un second deck fige dans le
    catalogue de la salle -- necessaire pour verifier que round.configure sait
    aussi changer le deck du round."""
    standard = create_standard_deck()
    other = _second_deck(standard.vote_type)
    snapshots = [build_deck_snapshot(standard), build_deck_snapshot(other)]
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(
        code=code, vote_type=standard.vote_type, deck_snapshot=snapshots[0],
        deck_snapshots=snapshots, title="Retro",
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return room.code, fac.token, voter.token, other.pk


@pytest.mark.django_db(transaction=True)
async def test_facilitator_configures_a_prepared_round_and_everyone_receives_the_fact():
    code, fac_token, voter_token, other_deck_id = await database_sync_to_async(_make_room_with_two_decks)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    # item.add cree un round idle -- pas besoin de round.prepare pour ce test,
    # configure_round ne demande qu'un round existant et encore idle.
    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    added = await _drain_until(fac, "item.added")
    round_id = added["payload"]["roundId"]

    await fac.send_json_to({
        "v": 1, "type": "round.configure",
        "payload": {"roundId": round_id, "deckId": other_deck_id, "config": {}},
    })
    configured = await _drain_until(voter, "round.configured")
    changed = await _drain_until(voter, "deck.changed")

    assert configured["payload"]["roundId"] == round_id
    assert configured["payload"]["config"] == {}
    assert configured["payload"]["deckSnapshot"]["deckId"] == other_deck_id
    # deck.changed reste emis, meme forme que celui de deck.select/round.prepare :
    # les clients actuels le savent deja traiter sans nouveau gestionnaire.
    assert changed["payload"]["deckSnapshot"]["deckId"] == other_deck_id

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_voter_cannot_configure_a_round():
    code, _, voter_token = await database_sync_to_async(_make_room)()
    voter, _ = await _join(voter_token, code)

    await voter.send_json_to({
        "v": 1, "type": "round.configure", "payload": {"roundId": 1, "config": {}},
    })
    err = await _drain_until(voter, "error")

    assert err["payload"]["rejectedType"] == "round.configure"

    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_configuring_an_already_open_round_is_refused():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    added = await _drain_until(fac, "item.added")
    round_id = added["payload"]["roundId"]

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")

    await fac.send_json_to({
        "v": 1, "type": "round.configure", "payload": {"roundId": round_id, "config": {}},
    })
    err = await _drain_until(fac, "error")

    assert err["payload"]["rejectedType"] == "round.configure"

    await fac.disconnect()
