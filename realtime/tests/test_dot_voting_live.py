"""Les deux diffusions neuves de Dot Voting sur le contrat WebSocket (design
2026-09-12 §4-§5, brief tache 6a-5, contrat §8.7).

Deux portees DIFFERENTES, a ne pas confondre :

- `response.totals` : A TOUS, mais seulement si la config du round l'autorise
  (design §5). Le defaut est le SECRET -- prouver l'absence exige une
  barriere en aller-retour SUR LA CONNEXION DU VOTANT lui-meme, jamais une
  inspection precoce (piege deja rencontre dans ce depot : un test d'absence
  qui regarde trop tot passe sans rien verifier). Cette barriere n'ordonne
  QUE la connexion qui l'emet -- chaque connexion doit flusher la sienne.
  **Un seul aller-retour ne suffit PAS quand `comm` est la connexion qui a
  elle-meme declenche l'action** : voir la docstring de `_ping_pong_types`
  pour le mecanisme exact (deux taches concurrentes par connexion dans
  `channels.consumer`, departagees dans l'ordre de la liste quand les deux
  sont pretes en meme temps) -- decouvert empiriquement en ecrivant ce
  fichier, un seul `ping`/`pong` laissait passer un `response.totals` qui
  n'arrivait qu'au tour suivant.
- `response.pending` : au FACILITATEUR SEUL, filtre A L'EMISSION
  (`realtime/consumers.py::facilitation_event`) -- jamais un masquage cote
  client. Le votant ne doit JAMAIS le recevoir, meme apres sa propre
  barriere.
"""
import pytest
from channels.db import database_sync_to_async

from decks.seed import create_dot_voting_deck
from realtime.consumers import RoomConsumer
from realtime.tests.test_consumer import _drain_until, _join
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room
from rooms.snapshot import build_deck_snapshot


def _make_dot_voting_room():
    """Meme forme que `_make_room` (test_consumer.py), mais avec le deck sans
    carte de Dot Voting (design §7) -- necessaire pour que la strategie
    active resolve sur `dot_voting_v1` (`_round_resolution_strategy`,
    realtime/services.py)."""
    deck = create_dot_voting_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), title="Retro")
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return code, fac.token, voter.token


async def _two_items(fac):
    """Ajoute deux items via `item.add` (facilitateur) et renvoie
    `(round_id, item1_id, item2_id)` -- n=2, donc au plus 2 jetons par item
    et un budget de 4."""
    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Item 1"}})
    first = await _drain_until(fac, "item.added")
    round_id = first["payload"]["roundId"]
    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Item 2"}})
    second = await _drain_until(fac, "item.added", pred=lambda p: len(p["items"]) == 2)
    item1, item2 = [i["id"] for i in second["payload"]["items"]]
    return round_id, item1, item2


async def _ping_pong_types(comm, limit=8, rounds=2):
    """Barriere en aller-retour SUR LA CONNEXION `comm` : envoie `ping` sur
    CETTE connexion et draine jusqu'au `pong`, en collectant les types
    croises en chemin. Ne prouve une absence QUE pour la connexion qui
    l'emet -- une autre connexion doit flusher la sienne (piege brief
    tache 6a-5).

    `rounds=2`, et non 1 -- DECOUVERT EN ECRIVANT CE TEST, a consigner :
    quand `comm` est la connexion qui a elle-meme declenche l'action (ici,
    le votant qui vient d'emettre `response.cast`), un SEUL aller-retour ne
    suffit pas a prouver une absence. `channels.utils.await_many_dispatch`
    (le coeur du consumer, `channels/consumer.py`) fait courir DEUX taches
    concurrentes par connexion -- `receive` (les messages client, dont notre
    `ping`) et `channel_receive` (les diffusions de groupe, dont un
    `response.totals` que CETTE MEME connexion vient de declencher) -- et
    les DEPARTAGE, quand les deux sont pretes en meme temps, dans l'ordre de
    la liste `[receive, self.channel_receive]` : le `ping` (deja en file au
    moment du `response.cast`) est alors dispatche AVANT la diffusion que ce
    `response.cast` vient lui-meme de declencher, qui n'atteint donc le
    correspondant qu'au tour SUIVANT. Un seul `ping`/`pong` peut ainsi
    renvoyer `pong` avant que le `response.totals` declenche par CE MEME
    message n'ait ete livre -- un test qui s'arreterait la NE PROUVERAIT
    RIEN (piege deja rencontre dans ce depot sous une autre forme : un test
    d'absence qui regarde trop tot passe sans rien verifier). Un second
    aller-retour couvre ce tour supplementaire. Verifie empiriquement : avec
    un seul `ping`/`pong`, `response.totals` apparaissait apres le premier
    `pong`, dans le SECOND aller-retour."""
    seen = []
    for _ in range(rounds):
        await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
        for _ in range(limit):
            msg = await comm.receive_json_from()
            seen.append(msg["type"])
            if msg["type"] == "pong":
                break
        else:
            raise AssertionError(f"pong not received, seen={seen}")
    return seen


@pytest.mark.django_db(transaction=True)
async def test_no_totals_leak_before_reveal_in_secret_mode_by_default():
    """Le defaut est le secret (design §5, derniere phrase) : sans
    `round.configure` prealable, `Round.config == {}` -- `response.cast` ne
    diffuse donc AUCUN `response.totals`. Preuve par barriere sur la
    connexion du VOTANT lui-meme (celle qui a emis `response.cast`), pas sur
    celle du facilitateur -- l'un ne prouve rien pour l'autre."""
    code, fac_token, voter_token = await database_sync_to_async(_make_dot_voting_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    _, item1, _item2 = await _two_items(fac)
    await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")
    await _drain_until(voter, "participation.update")

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item1, "payload": {"points": 2}}}
    )
    seen = await _ping_pong_types(voter)

    # Sanity : le drain n'est pas trivialement vide -- la reponse a bien
    # declenche AU MOINS une diffusion (participation.update), sans quoi
    # l'absence de response.totals ne prouverait rien.
    assert "participation.update" in seen
    assert "response.totals" not in seen

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_no_totals_leak_before_reveal_when_the_config_explicitly_turns_it_off():
    code, fac_token, voter_token = await database_sync_to_async(_make_dot_voting_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    round_id, item1, _item2 = await _two_items(fac)
    await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    await fac.send_json_to({
        "v": 1, "type": "round.configure",
        "payload": {"roundId": round_id, "config": {"liveTotals": False}},
    })
    await _drain_until(voter, "round.configured")

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")
    await _drain_until(voter, "participation.update")

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item1, "payload": {"points": 2}}}
    )
    seen = await _ping_pong_types(voter)

    assert "participation.update" in seen
    assert "response.totals" not in seen

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_totals_are_broadcast_to_everyone_and_carry_no_identity_when_the_config_allows_it():
    """Mode visible (design §5) : le total EST diffuse, A TOUS -- mais reste
    un AGREGAT pur. Verifie sur la connexion du FACILITATEUR (un tiers a la
    reponse du votant) : si meme lui ne recoit qu'un total sans identite,
    l'invariant tient pour tout le monde."""
    code, fac_token, voter_token = await database_sync_to_async(_make_dot_voting_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    round_id, item1, item2 = await _two_items(fac)
    await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    await fac.send_json_to({
        "v": 1, "type": "round.configure",
        "payload": {"roundId": round_id, "config": {"liveTotals": True}},
    })
    await _drain_until(fac, "round.configured")

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")
    await _drain_until(fac, "participation.update")

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item1, "payload": {"points": 2}}}
    )
    totals = await _drain_until(fac, "response.totals")

    blocks = {b["itemId"]: b for b in totals["payload"]["itemResults"]}
    assert blocks[item1]["totalPoints"] == 2
    assert blocks[item1]["responseCount"] == 1
    assert blocks[item2]["totalPoints"] == 0
    # Aucune cle nominative : ni `votes`, ni `participantId`, ni rien d'autre
    # que ce qu'un agregat porte deja.
    assert set(blocks[item1].keys()) == {"itemId", "totalPoints", "responseCount"}
    assert set(blocks[item2].keys()) == {"itemId", "totalPoints", "responseCount"}

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_remaining_budget_reaches_the_facilitator_but_never_the_voter():
    """`response.pending` (le reste a placer par participant) : au
    facilitateur SEUL. Le votant, meme apres SA PROPRE barriere en
    aller-retour, ne le voit jamais -- le filtrage se fait a l'emission
    (`facilitation_event`), pas par un masquage cote client."""
    code, fac_token, voter_token = await database_sync_to_async(_make_dot_voting_room)()
    fac, fac_sync = await _join(fac_token, code)
    voter, voter_sync = await _join(voter_token, code)
    voter_public_id = voter_sync["payload"]["myParticipantId"]

    _, item1, _item2 = await _two_items(fac)
    await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")
    await _drain_until(fac, "participation.update")
    await _drain_until(voter, "vote.opened")
    await _drain_until(voter, "participation.update")

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item1, "payload": {"points": 2}}}
    )

    pending = await _drain_until(fac, "response.pending")
    assert pending["payload"]["remaining"][voter_public_id] == 2  # n=2 -> budget 4, 2 poses -> reste 2

    seen = await _ping_pong_types(voter)
    assert "participation.update" in seen
    assert "response.pending" not in seen

    await fac.disconnect()
    await voter.disconnect()


# --- Ce qui invalide silencieusement l'affichage sans nouveau jeton (round
# de correction 2, brief) : ajouter un item change n (donc le budget) et
# reinitialiser vide les reponses. Les deux doivent rafraichir les faits
# ci-dessus, sans attendre le prochain `response.cast`.


@pytest.mark.django_db(transaction=True)
async def test_adding_an_item_mid_round_refreshes_totals_and_pending_budgets():
    code, fac_token, voter_token = await database_sync_to_async(_make_dot_voting_room)()
    fac, _ = await _join(fac_token, code)
    voter, voter_sync = await _join(voter_token, code)
    voter_public_id = voter_sync["payload"]["myParticipantId"]

    round_id, item1, _item2 = await _two_items(fac)  # n=2 -> budget 4
    await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    await fac.send_json_to({
        "v": 1, "type": "round.configure",
        "payload": {"roundId": round_id, "config": {"liveTotals": True}},
    })
    await _drain_until(fac, "round.configured")

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")
    await _drain_until(fac, "participation.update")

    # n=2 -> budget 4. Le votant en pose 2 : il en reste 2.
    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item1, "payload": {"points": 2}}}
    )
    before = await _drain_until(fac, "response.pending")
    assert before["payload"]["remaining"][voter_public_id] == 2

    # Le facilitateur ajoute un TROISIEME item : n passe a 3, budget a 6.
    # Les 2 jetons deja poses laissent maintenant 4, pas 2 -- la preuve que
    # la valeur a bien ete RECALCULEE, pas simplement rejouee.
    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Item 3"}})
    after = await _drain_until(fac, "response.pending")
    assert after["payload"]["remaining"][voter_public_id] == 4

    # Les totaux en direct, eux, sont publics : verifies sur la connexion du
    # VOTANT (tiers a ce geste du facilitateur) -- ils portent desormais
    # trois blocs, le troisieme a zero.
    #
    # `limit` releve : la connexion du votant n'a ete draine QU'UNE fois
    # depuis sa jointure (le premier `_drain_until` ci-dessus), donc son
    # tampon porte encore le bruit accumule depuis (agenda.updated/
    # subject.updated des deux `item.add` de `_two_items`, `round.configured`,
    # `vote.opened`, deux `participation.update`, le premier `response.totals`
    # a 2 items, puis `item.added`/`agenda.updated`/`subject.updated` du
    # troisieme item) -- 11 messages avant celui vise (piege brief tache
    # 6a-5 : « le helper abandonne au bout de 8, compte ce que tu diffuses »).
    totals = await _drain_until(voter, "response.totals", pred=lambda p: len(p["itemResults"]) == 3, limit=15)
    assert len(totals["payload"]["itemResults"]) == 3

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_vote_reset_refreshes_pending_budgets_to_the_full_amount():
    code, fac_token, voter_token = await database_sync_to_async(_make_dot_voting_room)()
    fac, _ = await _join(fac_token, code)
    voter, voter_sync = await _join(voter_token, code)
    voter_public_id = voter_sync["payload"]["myParticipantId"]

    _, item1, _item2 = await _two_items(fac)  # n=2 -> budget 4
    await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(fac, "vote.opened")
    await _drain_until(fac, "participation.update")

    await voter.send_json_to(
        {"v": 1, "type": "response.cast", "payload": {"itemId": item1, "payload": {"points": 2}}}
    )
    before = await _drain_until(fac, "response.pending")
    assert before["payload"]["remaining"][voter_public_id] == 2

    # Reinitialiser vide les reponses : le reste a placer doit refleter le
    # budget PLEIN aussitot, pas rester bloque sur l'ancienne valeur jusqu'au
    # prochain jeton pose sur le round suivant.
    await fac.send_json_to({"v": 1, "type": "vote.reset", "payload": {}})
    after = await _drain_until(fac, "response.pending")
    assert after["payload"]["remaining"][voter_public_id] == 4

    await fac.disconnect()
    await voter.disconnect()


# --- Echec ferme du filtre facilitateur-seul (relecture round de
# correction 2) -----------------------------------------------------------
#
# Sous l'ANCIEN mecanisme (chaque connexion se re-resolvait elle-meme via
# `is_facilitator()`), une information manquante dans l'evenement n'avait
# aucune consequence. Avec la comparaison d'identifiants (round de
# correction 2, optimisation), ce n'est plus vrai : si `audienceId` est
# absent de l'evenement, le comparer nu a `self.public_id` peut reussir PAR
# ACCIDENT pour une connexion dont l'identifiant public n'est pas encore
# etabli (`None == None`) -- livrant alors le fait a TOUT LE MONDE, l'inverse
# exact de ce que ce filtre existe pour garantir. Teste directement au
# niveau de `facilitation_event` (pas via le cycle WS complet) : c'est le
# point d'accroche exact du defaut, et ce test survit a tout remaniement de
# ce qui l'entoure.


async def test_a_facilitator_only_event_without_an_audience_id_reaches_nobody():
    """Meme un destinataire dont l'identifiant public serait deja etabli ne
    doit RIEN recevoir si l'evenement lui-meme ne porte pas `audienceId` --
    l'absence cote emetteur doit fermer le filtre, jamais l'ouvrir."""
    consumer = RoomConsumer()
    consumer.public_id = "p-1"
    emitted = []

    async def _record_emit(mtype, payload, cid=None):
        emitted.append((mtype, payload))

    consumer._emit = _record_emit

    await consumer.facilitation_event(
        {"mtype": "response.pending", "payload": {"remaining": {}}, "audience": "facilitator"}
    )

    assert emitted == []


async def test_a_facilitator_only_event_without_an_audience_id_reaches_a_connection_with_no_public_id_either():
    """Le cas precis qui motive ce test (brief) : une connexion dont
    l'identifiant public n'est PAS ENCORE etabli (pas de jointure
    `session.join` complete -- `self.public_id` n'existe pas du tout comme
    attribut) ne doit pas recevoir un fait reserve juste parce que
    `getattr(self, "public_id", None)` et `event.get("audienceId")` valent
    tous deux `None` : la coincidence qui livrerait le fait a tout le
    monde si le garde-fou `audience_id is None` n'existait pas."""
    consumer = RoomConsumer()
    # PAS de consumer.public_id : simule une connexion avant sa jointure.
    assert not hasattr(consumer, "public_id")
    emitted = []

    async def _record_emit(mtype, payload, cid=None):
        emitted.append((mtype, payload))

    consumer._emit = _record_emit

    await consumer.facilitation_event(
        {"mtype": "response.pending", "payload": {"remaining": {}}, "audience": "facilitator"}
    )

    assert emitted == []
