"""Les nouveaux types item.* et la survie des alias herites (design §5).

Les alias existent pour que cette livraison soit PUREMENT back : le front ne
bascule qu'en 5b. Un test qui les couvre est donc un test de deploiement, pas une
politesse.
"""
import pytest
from channels.db import database_sync_to_async

from realtime.tests.test_consumer import _drain_until, _join, _make_room


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
async def test_legacy_subject_set_still_works():
    """Le front d'aujourd'hui n'envoie que `subject.set` : il doit continuer a
    jouer, sans modification, pendant toute la livraison 5a."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget ?"}})
    msg = await _drain_until(voter, "subject.updated")

    assert msg["payload"]["text"] == "Budget ?"
    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_round_select_replays_the_agenda_id():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "subject.add", "payload": {"text": "Q1"}})
    await fac.send_json_to({"v": 1, "type": "subject.add", "payload": {"text": "Q2"}})
    agenda_msg = await _drain_until(fac, "agenda.updated", pred=lambda p: len(p["agenda"]) == 2)
    second = agenda_msg["payload"]["agenda"][1]["id"]

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
    await voter.send_json_to({"v": 1, "type": "vote.cast", "payload": {"cardValue": "4"}})
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
