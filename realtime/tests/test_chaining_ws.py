"""Chainage (design 2026-09-11 §7) sur le contrat WebSocket (§8.5, tache 3).

`realtime/services.py::bind_round`/`chaining_candidates`/`resolve_source` (taches
1-2, voir task-2-report.md) portent toute la logique de domaine ; ces tests ne
verifient que le cablage consumer <-> contrat : round.bind/round.resolve en
entree, round.bound/round.resolved en sortie, et le fait round.candidates,
reserve au facilitateur et filtre a l'emission (jamais une diffusion de groupe
suivie d'un masquage cote client).
"""
import pytest
from channels.db import database_sync_to_async

from rooms.models import Item, Participant, Room, Round
from realtime.tests.test_consumer import _drain_until, _join, _make_room
from realtime.tests.test_scenario_ws import _collect_until_pong, _settle_join


def _two_rounds(code, fac_token, source_items=("Un", "Deux"), consumer_strategy=None):
    """Une source (avec ses items) et une cible, deja en base AVANT que les
    sockets ne rejoignent -- comme le ferait un facilitateur qui a deja
    compose son scenario. Ni l'une ni l'autre ne devient `room.current_round`
    (cree par ORM direct, pas par un service) : les tests choisissent
    eux-memes, via `round.select`, quand un round devient courant.

    `consumer_strategy`, quand fourni, pose un `deck_snapshot` dont la
    strategie de resolution est etrangere au registre (`consumes="none"`
    faute d'entree) -- c'est ce qui permet de tester un refus de liaison
    incompatible sur deux rounds REELS, plutot que sur un identifiant
    inexistant (piege deja documente : le refus doit venir de l'incompatibilite,
    pas de l'inexistence)."""
    room = Room.objects.get(code=code)
    fac = Participant.objects.get(token=fac_token)
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    for n, text in enumerate(source_items, start=1):
        Item.objects.create(round=source, text=text, sequence=n)
    consumer_kwargs = {}
    if consumer_strategy:
        consumer_kwargs["deck_snapshot"] = {"resolutionStrategy": consumer_strategy, "cards": []}
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2, **consumer_kwargs)
    return source.id, consumer.id


def _item_id(round_id, text):
    return Item.objects.get(round_id=round_id, text=text).id


@pytest.mark.django_db(transaction=True)
async def test_bind_declares_a_linkage_and_broadcasts_it_to_everyone():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    source_id, consumer_id = await database_sync_to_async(_two_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)
    voter, _ = await _join(voter_token, code)
    await _settle_join(voter)
    assert set(await _collect_until_pong(fac)) <= {"participant.joined", "facilitator.presence"}

    rule = {"take": "items", "mode": "manual", "top": None}
    await fac.send_json_to({"v": 1, "type": "round.bind", "payload": {
        "roundId": consumer_id, "sourceRoundId": source_id, "rule": rule,
    }})
    fac_bound = await _drain_until(fac, "round.bound")
    voter_bound = await _drain_until(voter, "round.bound")
    expected = {"roundId": consumer_id, "sourceRoundId": source_id, "rule": rule}
    assert fac_bound["payload"] == expected
    assert voter_bound["payload"] == expected

    # round.bind ne diffuse QUE round.bound : la liaison ne change ni les items
    # ni l'etat d'aucun round, un fait de plus ferait echouer ce test pour la
    # mauvaise raison (piege deja rencontre dans ce depot).
    assert await _collect_until_pong(fac) == []
    assert await _collect_until_pong(voter) == []

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_voter_is_refused_both_chaining_intentions():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    source_id, consumer_id = await database_sync_to_async(_two_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)
    voter, _ = await _join(voter_token, code)
    await _settle_join(voter)
    assert set(await _collect_until_pong(fac)) <= {"participant.joined", "facilitator.presence"}

    rule = {"take": "items", "mode": "manual", "top": None}
    await voter.send_json_to({"v": 1, "type": "round.bind", "payload": {
        "roundId": consumer_id, "sourceRoundId": source_id, "rule": rule,
    }})
    err = await _drain_until(voter, "error")
    assert err["payload"]["code"] == "forbidden.not_facilitator"
    assert err["payload"]["rejectedType"] == "round.bind"

    await voter.send_json_to({"v": 1, "type": "round.resolve", "payload": {"roundId": consumer_id}})
    err = await _drain_until(voter, "error")
    assert err["payload"]["code"] == "forbidden.not_facilitator"
    assert err["payload"]["rejectedType"] == "round.resolve"

    # Les deux refus sont survenus avant toute ecriture : rien n'a ete diffuse
    # au facilitateur, qui n'a rien envoye.
    assert await _collect_until_pong(fac) == []

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_bind_refuses_an_incompatible_linkage_between_two_real_rounds():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    source_id, consumer_id = await database_sync_to_async(_two_rounds)(
        code, fac_token, consumer_strategy="une_strategie_inconnue",
    )
    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)

    rule = {"take": "items", "mode": "auto", "top": None}
    await fac.send_json_to({"v": 1, "type": "round.bind", "payload": {
        "roundId": consumer_id, "sourceRoundId": source_id, "rule": rule,
    }})
    err = await _drain_until(fac, "error")
    assert err["payload"]["code"] == "state.invalid_transition"
    assert err["payload"]["rejectedType"] == "round.bind"

    # Le refus a precede toute ecriture.
    consumer = await database_sync_to_async(Round.objects.get)(id=consumer_id)
    assert consumer.source_round_id is None

    await fac.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_auto_binding_resolves_on_its_own_when_the_round_becomes_current():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    source_id, consumer_id = await database_sync_to_async(_two_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)

    rule = {"take": "items", "mode": "auto", "top": None}
    await fac.send_json_to({"v": 1, "type": "round.bind", "payload": {
        "roundId": consumer_id, "sourceRoundId": source_id, "rule": rule,
    }})
    await _drain_until(fac, "round.bound")

    await fac.send_json_to({"v": 1, "type": "round.select", "payload": {"roundId": consumer_id}})
    selected = await _drain_until(fac, "round.selected")
    assert [item["text"] for item in selected["payload"]["items"]] == ["Un", "Deux"]
    await _drain_until(fac, "subject.updated")
    await _drain_until(fac, "agenda.updated")

    # Mode auto : deja resolu par select_round lui-meme (realtime/services.py),
    # round.selected porte deja les items resultants -- aucun round.candidates,
    # aucun fait de plus.
    assert await _collect_until_pong(fac) == []

    await fac.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_manual_binding_presents_candidates_to_the_facilitator_only():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    source_id, consumer_id = await database_sync_to_async(_two_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)
    voter, _ = await _join(voter_token, code)
    await _settle_join(voter)
    assert set(await _collect_until_pong(fac)) <= {"participant.joined", "facilitator.presence"}

    rule = {"take": "items", "mode": "manual", "top": None}
    await fac.send_json_to({"v": 1, "type": "round.bind", "payload": {
        "roundId": consumer_id, "sourceRoundId": source_id, "rule": rule,
    }})
    await _drain_until(fac, "round.bound")
    await _drain_until(voter, "round.bound")

    await fac.send_json_to({"v": 1, "type": "round.select", "payload": {"roundId": consumer_id}})

    # Le fait des candidats arrive sur la connexion du facilitateur -- jamais
    # diffuse au groupe : il n'est ni precede ni suivi d'un message que le
    # votant devrait ignorer, c'est le serveur qui filtre, pas le client.
    candidates = await _drain_until(fac, "round.candidates")
    assert candidates["payload"]["roundId"] == consumer_id
    got = {(c["sourceItemId"], c["text"]) for c in candidates["payload"]["candidates"]}
    un_id = await database_sync_to_async(_item_id)(source_id, "Un")
    deux_id = await database_sync_to_async(_item_id)(source_id, "Deux")
    assert got == {(un_id, "Un"), (deux_id, "Deux")}

    # Par cet instant, round.select a deja entierement termine sur la
    # connexion du facilitateur (on vient d'y lire round.candidates, le
    # dernier message qu'il emet) : tout ce que round.select devait diffuser
    # au groupe est donc deja arrive au votant. _collect_until_pong(voter)
    # recolte tout ce qu'il a recu avant son propre pong -- round.candidates
    # n'y figure pas, ce qui le prouve explicitement (un simple drain
    # l'aurait silencieusement ignore en le cherchant).
    voter_types = await _collect_until_pong(voter)
    assert set(voter_types) == {"vote.wasReset", "round.selected", "subject.updated", "agenda.updated"}

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_candidates_arrive_before_the_round_select_broadcasts_on_the_facilitators_own_connection():
    """Ordre contre-intuitif, documente au contrat (§8.5.a) : sur SA PROPRE
    connexion, round.select emet ses candidats par ecriture directe pendant
    que le handler tourne encore, alors que ses quatre diffusions de groupe
    ne reviennent au facilitateur qu'apres avoir repasse par la boucle de
    distribution du channel layer -- qui ne reprend la main qu'apres que ce
    meme handler a rendu la sienne. round.candidates arrive donc EN PREMIER
    sur cette connexion, avant meme round.selected, alors que le code les
    emet dans l'ordre inverse. Sans ce test, une reorganisation du traitement
    pourrait inverser cet ordre sans qu'aucun autre test (qui cherchent un
    type au fil de l'eau, sans imposer d'ordre) ne le remarque."""
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    source_id, consumer_id = await database_sync_to_async(_two_rounds)(code, fac_token)
    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)

    rule = {"take": "items", "mode": "manual", "top": None}
    await fac.send_json_to({"v": 1, "type": "round.bind", "payload": {
        "roundId": consumer_id, "sourceRoundId": source_id, "rule": rule,
    }})
    await _drain_until(fac, "round.bound")

    await fac.send_json_to({"v": 1, "type": "round.select", "payload": {"roundId": consumer_id}})
    types = [(await fac.receive_json_from())["type"] for _ in range(5)]
    assert types == [
        "round.candidates", "vote.wasReset", "round.selected", "subject.updated", "agenda.updated",
    ]

    await fac.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_manual_resolution_copies_exactly_what_was_checked_and_broadcasts_it():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    source_id, consumer_id = await database_sync_to_async(_two_rounds)(
        code, fac_token, source_items=("Garder", "Ecarter"),
    )
    keep_id = await database_sync_to_async(_item_id)(source_id, "Garder")

    fac, _ = await _join(fac_token, code)
    await _settle_join(fac)
    voter, _ = await _join(voter_token, code)
    await _settle_join(voter)
    assert set(await _collect_until_pong(fac)) <= {"participant.joined", "facilitator.presence"}

    rule = {"take": "items", "mode": "manual", "top": None}
    await fac.send_json_to({"v": 1, "type": "round.bind", "payload": {
        "roundId": consumer_id, "sourceRoundId": source_id, "rule": rule,
    }})
    await _drain_until(fac, "round.bound")
    await _drain_until(voter, "round.bound")

    await fac.send_json_to({"v": 1, "type": "round.select", "payload": {"roundId": consumer_id}})
    await _drain_until(fac, "round.candidates")
    await _collect_until_pong(voter)  # purge le tail de round.select cote votant

    await fac.send_json_to({"v": 1, "type": "round.resolve", "payload": {
        "roundId": consumer_id, "sourceItemIds": [keep_id],
    }})
    resolved = await _drain_until(fac, "round.resolved")
    assert resolved["payload"]["roundId"] == consumer_id
    items = resolved["payload"]["items"]
    assert [i["text"] for i in items] == ["Garder"]
    assert items[0]["sourceItemId"] == keep_id
    assert items[0]["originItemId"] == keep_id

    voter_resolved = await _drain_until(voter, "round.resolved")
    assert voter_resolved["payload"] == resolved["payload"]

    await fac.disconnect()
    await voter.disconnect()
