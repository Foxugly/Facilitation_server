"""`response.cast` dans le contrat WS (contrat §8.2).

`response.cast` est ouvert a tous les participants (pas une intention de controle).
L'alias herite `vote.cast` et les cles plates de `vote.revealed` sont retires en fin
de 5b (contrat §8.2.b) : `itemResults` est desormais la seule forme du decompte.
"""
import pytest
from channels.db import database_sync_to_async

from realtime.tests.test_consumer import _drain_until, _join, _make_room


@pytest.mark.django_db(transaction=True)
async def test_response_cast_on_two_items_does_not_overwrite_either():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Q1"}})
    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Q2"}})
    added = await _drain_until(fac, "item.added", pred=lambda p: len(p["items"]) == 2)
    item1, item2 = [i["id"] for i in added["payload"]["items"]]

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")
    # `vote.open` diffuse aussi une premiere participation.update (etat vide) : la
    # consommer ici pour que les deux drains suivants portent chacun EXACTEMENT sur
    # le participation.update declenche par le response.cast qui le precede.
    await _drain_until(fac, "participation.update")

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item1, "payload": {"card": "4"}}}
    )
    # Un seul item repondu sur deux : pas encore complet (services._completed_participant_ids).
    after_first = await _drain_until(fac, "participation.update")
    assert after_first["payload"]["voted"] == 0

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item2, "payload": {"card": "6"}}}
    )
    complete = await _drain_until(fac, "participation.update")
    assert complete["payload"]["voted"] == 1
    assert complete["payload"]["total"] == 2
    assert len(complete["payload"]["votedIds"]) == 1

    await fac.send_json_to({"v": 1, "type": "vote.reveal", "payload": {}})
    revealed = await _drain_until(fac, "vote.revealed")
    results = {block["itemId"]: block["tally"] for block in revealed["payload"]["itemResults"]}
    assert results[item1] == [{"cardValue": "4", "count": 1}]
    assert results[item2] == [{"cardValue": "6", "count": 1}]

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_vote_revealed_carries_item_results_only():
    """`vote.revealed` ne porte plus que `itemResults` : les cles plates
    (`tally`/`spread`/`votes` au premier niveau) et l'alias entrant `vote.cast`
    sont retires en fin de 5b (contrat §8.2.b)."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Q1"}})
    added = await _drain_until(fac, "item.added")
    item_id = added["payload"]["itemId"]
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")
    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item_id, "payload": {"card": "4"}}}
    )
    await _drain_until(fac, "participation.update", pred=lambda p: p["voted"] == 1)

    await fac.send_json_to({"v": 1, "type": "vote.reveal", "payload": {}})
    revealed = await _drain_until(fac, "vote.revealed")
    payload = revealed["payload"]

    assert set(payload.keys()) == {"itemResults", "anonymous", "reason"}
    assert payload["itemResults"] == [
        {
            "itemId": item_id,
            "tally": [{"cardValue": "4", "count": 1}],
            "spread": {"min": 4, "max": 4},
            "anonymous": False,
            "votes": payload["itemResults"][0]["votes"],
        }
    ]
    assert payload["reason"] == "facilitator"

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_response_cast_on_an_item_from_another_round_gets_a_typed_error():
    code1, fac1_token, voter1_token = await database_sync_to_async(_make_room)()
    code2, fac2_token, _ = await database_sync_to_async(_make_room)()
    fac1, _ = await _join(fac1_token, code1)
    fac2, _ = await _join(fac2_token, code2)

    # Item d'un round appartenant a une AUTRE room, donc certainement pas au round
    # courant de code1.
    await fac2.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Foreign"}})
    foreign = await _drain_until(fac2, "item.added")
    foreign_item_id = foreign["payload"]["itemId"]

    await fac1.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Q1"}})
    await _drain_until(fac1, "item.added")
    await fac1.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac1, "vote.opened")

    # voter1 ne rejoint qu'apres : sa file ne porte que ses propres broadcasts de
    # join, pas la cascade item.add/vote.open de fac1 -- la limite de 8 messages de
    # _drain_until n'a pas a l'absorber pour atteindre l'erreur ci-dessous.
    voter1, _ = await _join(voter1_token, code1)

    await voter1.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": foreign_item_id, "payload": {"card": "4"}}}
    )
    err = await _drain_until(voter1, "error")
    assert err["payload"]["rejectedType"] == "response.cast"
    assert err["payload"]["code"] == "state.invalid_transition"

    await fac1.disconnect()
    await voter1.disconnect()
    await fac2.disconnect()
