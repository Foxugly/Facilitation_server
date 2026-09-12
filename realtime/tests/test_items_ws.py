"""Les nouveaux types item.* et la survie des alias herites de 5a (design §5).

`vote.cast` (5b) est retire : Facilitation_frontend n'en a plus besoin, verifie
en prod (contrat §8.2.b). `subject.*` (5a), en revanche, restent des alias
herites : le front de production les emet encore (`room-socket.service.ts`),
la bascule sur `item.*`/`round.select` n'a pas eu lieu. Un test qui les couvre
est donc un test de deploiement, pas une politesse.
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
    """Le front de production n'a pas encore bascule sur `item.*` : `subject.set`
    reste un alias herite tant que ce basculement n'est pas verifie en prod."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget ?"}})
    msg = await _drain_until(voter, "subject.updated")

    assert msg["payload"]["text"] == "Budget ?"
    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_production_sequence_subject_set_then_response_cast_via_agenda_item_id():
    """Verrouille la sequence exacte du front deploye : `subject.set` pour poser
    le sujet, l'`itemId` recupere dans l'entree COURANTE d'`agenda.updated`
    (chaque entree porte ses `items`, cf. `services.build_agenda`), puis
    `response.cast {itemId, payload}` sur cet id. Le front n'a pas d'autre
    source pour l'itemId tant qu'il n'a pas bascule sur `item.*` : si
    `build_agenda` cessait un jour d'emettre `items` par entree, ce test
    romprait avant que la prod ne le decouvre."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget ?"}})
    agenda_msg = await _drain_until(fac, "agenda.updated")
    current = next(e for e in agenda_msg["payload"]["agenda"] if e["status"] == "current")
    item_id = current["items"][0]["id"]

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item_id, "payload": {"card": "4"}}}
    )
    part = await _drain_until(fac, "participation.update", pred=lambda p: p["voted"] == 1)
    assert part["payload"]["total"] == 2

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
async def test_round_add_queues_a_new_agenda_entry():
    """`round.add` est le point d'entree qui remplacera l'alias herite
    `subject.add` quand le front basculera : ouvre un ROUND DE PLUS dans la
    file (le scenario), a NE PAS confondre avec `item.add` qui enrichit le
    round COURANT — deux semantiques distinctes (voir le commentaire de
    `_dispatch` dans `consumers.py`)."""
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "round.add", "payload": {"text": "Q1"}})
    await _drain_until(fac, "agenda.updated")
    await fac.send_json_to({"v": 1, "type": "round.add", "payload": {"text": "Q2"}})
    msg = await _drain_until(fac, "agenda.updated", pred=lambda p: len(p["agenda"]) == 2)

    agenda = msg["payload"]["agenda"]
    assert [x["text"] for x in agenda] == ["Q1", "Q2"]
    assert agenda[0]["status"] == "current" and agenda[1]["status"] == "pending"
    await fac.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_voter_cannot_round_add():
    """Le refus doit porter le nom de l'intention EMISE (`round.add`), pas un
    intitule fige interne : `add_scenario_item` sert aussi l'alias
    `subject.add`, qui doit refuser sous SON propre nom (voir
    `test_a_voter_cannot_subject_add` ci-dessous) — les deux ne doivent pas se
    confondre l'un l'autre."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    voter, _ = await _join(voter_token, code)

    await voter.send_json_to({"v": 1, "type": "round.add", "payload": {"text": "Q1"}})
    msg = await _drain_until(voter, "error")

    assert msg["payload"]["code"] == "forbidden.not_facilitator"
    assert msg["payload"]["rejectedType"] == "round.add"
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_voter_cannot_subject_add():
    """Meme garde que `round.add`, via l'alias herite : le refus doit porter
    `subject.add`, pas `round.add` ni l'ancien `item.add` fige — les deux
    chemins qui appellent `add_scenario_item` doivent rester distinguables."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    voter, _ = await _join(voter_token, code)

    await voter.send_json_to({"v": 1, "type": "subject.add", "payload": {"text": "Q1"}})
    msg = await _drain_until(voter, "error")

    assert msg["payload"]["code"] == "forbidden.not_facilitator"
    assert msg["payload"]["rejectedType"] == "subject.add"
    await voter.disconnect()


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
