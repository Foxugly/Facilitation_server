"""Config du round dans `state.sync` (dot voting design section 5, tache 6a
correction 3).

Le reglage de visibilite des totaux (`Round.config.liveTotals`) est une
propriete du round comme son etat ou ses items -- `state.sync` ne rejoue
aucun evenement (regle du depot), donc un facilitateur qui recharge sa page
PENDANT qu'il compose un round doit retrouver son interrupteur tel qu'il
l'a pose. Avant ce correctif, `state.sync` ne portait jamais cette config :
un rechargement faisait revenir l'ecran sur la valeur par defaut de
l'interface (« masque »), une FAUSSE ASSURANCE de confidentialite -- le sens
de l'erreur est le mauvais, contrairement a un simple affichage perime.

Cle A LA RACINE du snapshot (`payload["config"]`, pas imbriquee sous
`round`) -- c'est ainsi que `Facilitation_frontend` la lit (`s.config`).

Ce reglage n'est PAS un secret (contrairement aux totaux qu'il gouverne) :
tout destinataire le voit, facilitateur ou votant -- §5 du design le dit
explicitement, et rien ici ne filtre a l'emission.
"""
import pytest
from channels.db import database_sync_to_async

from decks.seed import create_dot_voting_deck
from realtime import services
from realtime.tests.test_consumer import _drain_until, _join
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Role, Room, Round
from rooms.snapshot import build_deck_snapshot


def _dot_voting_room():
    """Meme forme que `_make_dot_voting_room` (test_dot_voting_live.py) :
    le deck sans carte de Dot Voting est necessaire pour que `configure_round`
    accepte la cle `liveTotals` -- le poker n'en declare aucune dans son
    `config_schema` (registre, `realtime/activities.py`) et la refuserait."""
    deck = create_dot_voting_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), title="Retro")
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return room, fac, voter


@pytest.mark.django_db
def test_state_sync_carries_the_round_config_the_facilitator_set():
    room, fac, voter = _dot_voting_room()
    rnd = Round.objects.create(room=room, facilitator=fac, sequence=1, deck_snapshot=room.deck_snapshot)
    Item.objects.create(round=rnd, text="Un", sequence=1)
    room.current_round = rnd
    room.save(update_fields=["current_round"])

    services.configure_round(room, fac, rnd.id, config={"liveTotals": True})
    # `configure_round` ecrit via un objet Round RECHARGE depuis la DB
    # (`room.rounds.filter(...)`), distinct de l'instance mise en cache sur
    # `room.current_round` -- rafraichir EN PLACE pour observer l'ecriture,
    # exactement ce qu'un appel WS suivant verrait deja (chaque intention
    # recharge le participant/la room depuis la DB, `services.py::resolve_participant`).
    room.current_round.refresh_from_db()

    # Valeur concrete, et visible aux DEUX destinataires -- pas un secret.
    assert services.build_state_sync(fac)["config"] == {"liveTotals": True}
    assert services.build_state_sync(voter)["config"] == {"liveTotals": True}


@pytest.mark.django_db
def test_state_sync_round_config_defaults_to_an_empty_dict_before_any_configure():
    room, fac, _voter = _dot_voting_room()
    rnd = Round.objects.create(room=room, facilitator=fac, sequence=1, deck_snapshot=room.deck_snapshot)
    Item.objects.create(round=rnd, text="Un", sequence=1)
    room.current_round = rnd
    room.save(update_fields=["current_round"])

    # `Round.config` par defaut du modele -- jamais confondu avec un "oui"
    # (memes termes que `live_totals_payload`, realtime/services.py).
    assert services.build_state_sync(fac)["config"] == {}


@pytest.mark.django_db
def test_state_sync_round_config_is_empty_when_no_current_round():
    room, fac, _voter = _dot_voting_room()

    payload = services.build_state_sync(fac)

    assert payload["round"] == {"id": None, "state": "idle"}
    assert payload["config"] == {}


@pytest.mark.django_db(transaction=True)
async def test_reloading_after_configuring_live_totals_keeps_the_setting_visible():
    """Reproduction fidele du defaut rapporte : le facilitateur active
    `liveTotals`, "recharge sa page" (deconnexion puis reconnexion du MEME
    token), et doit retrouver l'interrupteur sur "visible" -- pas revenir au
    defaut secret de l'ecran. `state.sync` est le seul message recu a la
    reconnexion (contrat §5.1) : aucun evenement n'est rejoue."""
    def _room_codes():
        room, fac, voter = _dot_voting_room()
        return room.code, fac.token, voter.token

    code, fac_token, _voter_token = await database_sync_to_async(_room_codes)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Item 1"}})
    added = await _drain_until(fac, "item.added")
    round_id = added["payload"]["roundId"]

    await fac.send_json_to({
        "v": 1, "type": "round.configure",
        "payload": {"roundId": round_id, "config": {"liveTotals": True}},
    })
    await _drain_until(fac, "round.configured")

    await fac.disconnect()

    fac2, sync2 = await _join(fac_token, code)

    assert sync2["payload"]["config"] == {"liveTotals": True}

    await fac2.disconnect()
