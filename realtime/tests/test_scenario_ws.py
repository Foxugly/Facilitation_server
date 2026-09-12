"""round.reorder / round.remove sur le contrat WebSocket (design 2026-09-12 5d,
contrat SS8.4). `realtime/services.py::reorder_rounds`/`remove_round` portent la
logique de domaine (tache 2, voir task-2-report.md) ; ces tests ne verifient que
le cablage consumer <-> contrat.

Verifie au passage l'hypothese du plan : aucun fait nouveau n'est necessaire, le
seul `agenda.updated` rediffuse suffit. Elle tient parce que ni `reorder_rounds`
ni `remove_round` ne changent jamais `room.current_round_id` -- le premier ne
touche qu'a `Round.sequence`, le second REFUSE explicitement de retirer le round
courant (voir `realtime/services.py::remove_round`, garde 3). L'id du round
courant que porte l'agenda (`status: "current"`) reste donc exact sans rien
ajouter -- c'est d'ailleurs deja ce que `Facilitation_frontend` en tire
(`room-socket.service.ts::applyEvent`, cas `agenda.updated`).
"""
import pytest
from channels.db import database_sync_to_async

from realtime import services
from realtime.tests.helpers import cast_first_item
from realtime.tests.test_consumer import _drain_until, _join, _make_room
from rooms.models import Participant, Room, Round


def _three_rounds(code, fac_token):
    """A, B, C dans cet ordre, poses AVANT que les sockets ne rejoignent : la
    file existe deja quand les deux participants se connectent, comme le
    ferait une salle ou le facilitateur a compose son scenario en amont."""
    room = Room.objects.get(code=code)
    fac = Participant.objects.get(token=fac_token)
    a_id = services.add_scenario_item(room, fac, "A")
    b_id = services.add_scenario_item(room, fac, "B")
    c_id = services.add_scenario_item(room, fac, "C")
    return a_id, b_id, c_id


def _acted_round_not_current(code, fac_token, voter_token):
    """A acte puis le courant bascule sur B -- pour que le refus de retirer A
    porte bien sur la garde du `Result` (round.remove garde 1), pas sur celle
    du round courant (garde 3) : meme isolation que
    `test_scenario.py::test_removing_a_round_that_carries_a_result_is_refused`."""
    room = Room.objects.get(code=code)
    fac = Participant.objects.get(token=fac_token)
    voter = Participant.objects.get(token=voter_token)
    a_id = services.add_scenario_item(room, fac, "A")
    b_id = services.add_scenario_item(room, fac, "B")
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)
    services.act_result(room, fac, "4")
    services.select_round(room, fac, b_id)
    return a_id, b_id


async def _collect_until_pong(comm):
    """Barriere par aller-retour (jamais de sleep) : ce que `comm` a recu AVANT
    son propre pong, types seuls. Le consumer traite les messages d'une meme
    connexion en serie, donc tout ce qui devait lui etre diffuse est deja
    arrive quand le pong revient."""
    await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
    types = []
    for _ in range(8):
        msg = await comm.receive_json_from()
        if msg["type"] == "pong":
            return types
        types.append(msg["type"])
    raise AssertionError("pong not received")


async def _settle_join(comm):
    """A appeler juste apres `_join(comm, ...)` : un ping/pong sur CETTE MEME
    connexion garantit que `_handle_join` (donc ses propres diffusions
    `participant.joined`/`facilitator.presence`) s'est entierement termine
    avant qu'on ne s'appuie sur leur absence ailleurs. Un flush via le ping
    d'UNE AUTRE connexion ne le garantirait pas : rien n'ordonne le traitement
    de ce ping par rapport a la fin du `_handle_join`, encore en cours, d'une
    connexion voisine -- piege rencontre en ecrivant ce fichier."""
    await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
    await _drain_until(comm, "pong")


@pytest.mark.django_db(transaction=True)
async def test_reorder_is_broadcast_to_everyone_as_agenda_updated():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    a_id, b_id, c_id = await database_sync_to_async(_three_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({
        "v": 1, "type": "round.reorder", "payload": {"roundIds": [c_id, a_id, b_id]},
    })
    fac_agenda = await _drain_until(fac, "agenda.updated")
    voter_agenda = await _drain_until(voter, "agenda.updated")

    assert [e["id"] for e in fac_agenda["payload"]["agenda"]] == [c_id, a_id, b_id]
    assert [e["id"] for e in voter_agenda["payload"]["agenda"]] == [c_id, a_id, b_id]

    # round.reorder ne diffuse QUE agenda.updated -- un fait de plus ferait
    # echouer ce test pour la mauvaise raison (piege deja rencontre ici).
    assert await _collect_until_pong(fac) == []
    assert await _collect_until_pong(voter) == []

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_remove_is_broadcast_to_everyone_as_agenda_updated():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    a_id, b_id, c_id = await database_sync_to_async(_three_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "round.remove", "payload": {"roundId": b_id}})
    fac_agenda = await _drain_until(fac, "agenda.updated")
    voter_agenda = await _drain_until(voter, "agenda.updated")

    assert [e["id"] for e in fac_agenda["payload"]["agenda"]] == [a_id, c_id]
    assert [e["id"] for e in voter_agenda["payload"]["agenda"]] == [a_id, c_id]

    # round.remove ne diffuse QUE agenda.updated -- meme exigence que pour
    # round.reorder : l'identite du round courant (A, jamais touche par ce
    # retrait) reste lisible dans cet agenda, aucun fait en plus n'etait donc
    # necessaire.
    assert await _collect_until_pong(fac) == []
    assert await _collect_until_pong(voter) == []

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_voter_is_refused_both_scenario_intentions():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    a_id, b_id, c_id = await database_sync_to_async(_three_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)
    voter, _ = await _join(voter_token, code)
    await _settle_join(voter)
    # Les deux joins sont desormais garantis termines, diffusions comprises :
    # purge ce bruit de connexion avant de verifier une absence, sinon la
    # barriere finale le confondrait avec une diffusion du refus.
    assert set(await _collect_until_pong(fac)) <= {"participant.joined", "facilitator.presence"}

    await voter.send_json_to({
        "v": 1, "type": "round.reorder", "payload": {"roundIds": [c_id, b_id, a_id]},
    })
    err = await _drain_until(voter, "error")
    assert err["payload"]["code"] == "forbidden.not_facilitator"
    assert err["payload"]["rejectedType"] == "round.reorder"

    await voter.send_json_to({"v": 1, "type": "round.remove", "payload": {"roundId": b_id}})
    err = await _drain_until(voter, "error")
    assert err["payload"]["code"] == "forbidden.not_facilitator"
    assert err["payload"]["rejectedType"] == "round.remove"

    # Les deux refus sont survenus avant toute ecriture : rien n'a ete
    # diffuse au facilitateur, qui n'a rien envoye.
    assert await _collect_until_pong(fac) == []

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_removing_an_acted_round_is_refused():
    """Round acte -- cree par l'etat, pas par un id inexistant : sinon le refus
    viendrait de l'absence du round et non de la garde (piege deja rencontre
    dans ce depot, voir CLAUDE.md SSPieges)."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    a_id, _b_id = await database_sync_to_async(_acted_round_not_current)(code, fac_token, voter_token)
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "round.remove", "payload": {"roundId": a_id}})
    err = await _drain_until(fac, "error")

    assert err["payload"]["code"] == "state.invalid_transition"
    assert err["payload"]["rejectedType"] == "round.remove"
    # Message distinct de celui du branchement "type inconnu" (meme code,
    # meme rejectedType) : sans cette assertion, le test passerait deja avant
    # tout cablage -- piege signale par le brief.
    assert err["payload"]["message"] == "Round already decided"
    assert await database_sync_to_async(Round.objects.filter(id=a_id).exists)()

    await fac.disconnect()
