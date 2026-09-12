"""Les types item.* du design §5, une fois les alias herites de 5a/5b retires.

Les alias `subject.*`/`vote.cast` existaient pour que cette livraison soit
PUREMENT back : le front a bascule sur `item.*`/`round.select`/`response.cast`,
et cette fenetre de compatibilite est refermee (contrat §8.1.b, §8.2.b).
"""
import pytest
from channels.db import database_sync_to_async

from realtime import services
from realtime.tests.test_consumer import _drain_until, _join, _make_room
from rooms.models import Participant, Room


@pytest.mark.django_db(transaction=True)
async def test_item_add_broadcasts_the_full_item_list():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Embauche ?"}})
    msg = await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    assert [i["text"] for i in msg["payload"]["items"]] == ["Budget ?", "Embauche ?"]
    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_voter_cannot_add_an_item():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    voter, _ = await _join(voter_token, code)

    await voter.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Mon post-it"}})
    msg = await _drain_until(voter, "error")

    assert msg["payload"]["rejectedType"] == "item.add"
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_item_add_broadcasts_the_subject_update():
    """`item.add` reste le seul chemin d'entree pour poser le texte du round
    courant : il doit diffuser `subject.updated`, exactement ce que faisait
    l'alias `subject.set` desormais retire (contrat §8.1.b)."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    msg = await _drain_until(voter, "subject.updated")

    assert msg["payload"]["text"] == "Budget ?"
    await fac.disconnect()
    await voter.disconnect()


def _add_scenario_item(code, token, text):
    """Ajoute une entree d'agenda directement au niveau service : depuis le
    retrait de l'alias `subject.add` (contrat §8.1.b), il n'existe plus de
    message WS pour composer un scenario a l'avance -- seul `round.select`
    (sous test ici) reste accessible depuis le WS."""
    room = Room.objects.get(code=code)
    participant = Participant.objects.get(token=token)
    return services.add_scenario_item(room, participant, text)


@pytest.mark.django_db(transaction=True)
async def test_round_select_replays_the_agenda_id():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Q1"}})
    await _drain_until(fac, "item.added")
    second = await database_sync_to_async(_add_scenario_item)(code, fac_token, "Q2")

    await fac.send_json_to({"v": 1, "type": "round.select", "payload": {"roundId": second}})
    msg = await _drain_until(fac, "round.selected")

    assert msg["payload"]["roundId"] == second
    assert [i["text"] for i in msg["payload"]["items"]] == ["Q2"]
    await fac.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_state_sync_carries_items_and_subject():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    await _drain_until(fac, "item.added")
    fac2, sync = await _join(fac_token, code)

    assert sync["payload"]["subject"] == "Budget ?"
    assert [i["text"] for i in sync["payload"]["items"]] == ["Budget ?"]
    await fac.disconnect()
    await fac2.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_item_remove_on_a_revealed_round_does_not_close_the_socket():
    """C1, vu du fil : `remove_item` sans garde d'etat laissait `result.act` sans
    item, et l'IntegrityError qui suivait n'etant pas un RoomError, `receive_json`
    la laissait remonter — la socket du facilitateur se fermait en pleine seance.
    Le refus doit etre un `error` ordinaire, et le round rester actable."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    added = await _drain_until(fac, "item.added")
    item_id = added["payload"]["itemId"]
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")
    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item_id, "payload": {"card": "4"}}}
    )
    await _drain_until(fac, "participation.update", pred=lambda p: p["voted"] == 1)
    await fac.send_json_to({"v": 1, "type": "vote.reveal", "payload": {}})
    await _drain_until(fac, "vote.revealed")

    await fac.send_json_to({"v": 1, "type": "item.remove", "payload": {"itemId": item_id}})
    err = await _drain_until(fac, "error")
    assert err["payload"]["rejectedType"] == "item.remove"

    await fac.send_json_to({"v": 1, "type": "result.act", "payload": {"chosenValue": "4"}})
    acted = await _drain_until(fac, "result.acted")
    assert acted["payload"]["chosenValue"] == "4"

    await fac.disconnect()
    await voter.disconnect()
