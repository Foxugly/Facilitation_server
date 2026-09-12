"""Chainage (contrat 8.5.a) a la reconnexion.

`round.select` presente deja les candidats au facilitateur quand un round lie
en mode manuel, pas encore resolu, devient courant -- mais seulement a cet
instant. Un facilitateur qui (re)connecte plus tard (rechargement de page)
ne rejoue jamais cet evenement : `state.sync` est le seul message qu'il
recoit, et il ne rejoue pas l'historique (contrat 5.1). Meme defaut, meme
remede que pour `itemResults` dans `test_state_sync_reveal.py`.

Le piege central est l'inverse : cette liste est reservee au facilitateur.
Un votant qui se connecte sur le MEME round ne doit voir apparaitre aucune
cle -- jamais un masquage cote client, une trame WebSocket se lit dans le
navigateur.
"""
import pytest

from realtime.services import bind_round, chaining_candidates, resolve_source, select_round
from realtime import services
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Role, Room, Round

pytestmark = pytest.mark.django_db


def _manual_rule(take="items", top=None):
    return {"take": take, "mode": "manual", "top": top}


@pytest.fixture
def room_with_facilitator_and_voter(standard_deck):
    from rooms.snapshot import build_deck_snapshot

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


def _bound_manual_consumer(room, fac):
    """Source a deux items, consumer lie en manuel, devenu courant via
    `round.select` mais pas encore resolu -- exactement la situation ou
    `round.candidates` part au facilitateur (contrat 8.5.a)."""
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    a = Item.objects.create(round=source, text="Un", sequence=1)
    b = Item.objects.create(round=source, text="Deux", sequence=2)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)
    bind_round(room, fac, consumer.id, source.id, _manual_rule())
    select_round(room, fac, consumer.id)
    return source, consumer, a, b


def test_facilitator_reconnecting_sees_the_chaining_candidates(room_with_facilitator_and_voter):
    room, fac, _voter = room_with_facilitator_and_voter
    source, consumer, a, b = _bound_manual_consumer(room, fac)

    payload = services.build_state_sync(fac)

    assert payload["chainingCandidates"] == chaining_candidates(room, consumer.id)
    assert [c["sourceItemId"] for c in payload["chainingCandidates"]] == [a.id, b.id]
    assert [c["text"] for c in payload["chainingCandidates"]] == ["Un", "Deux"]


def test_voter_connecting_on_the_same_round_does_not_see_the_candidates(
    room_with_facilitator_and_voter,
):
    """Le cas qui compte : la liste est reservee au facilitateur, et ne doit
    meme pas figurer (absente, pas vide) dans l'etat d'un votant."""
    room, fac, voter = room_with_facilitator_and_voter
    _bound_manual_consumer(room, fac)

    payload = services.build_state_sync(voter)

    assert "chainingCandidates" not in payload


def test_state_sync_has_no_candidates_when_no_source_is_bound(room_with_facilitator_and_voter):
    room, fac, _voter = room_with_facilitator_and_voter
    Round.objects.create(room=room, facilitator=fac, sequence=1)

    payload = services.build_state_sync(fac)

    assert "chainingCandidates" not in payload


def test_state_sync_has_no_candidates_once_the_binding_is_resolved(
    room_with_facilitator_and_voter,
):
    room, fac, _voter = room_with_facilitator_and_voter
    _source, consumer, a, _b = _bound_manual_consumer(room, fac)

    resolve_source(room, fac, consumer.id, item_ids=[a.id])

    # `resolve_source` relit le round par une requete neuve (round_id), separee
    # de l'objet `room.current_round` mis en cache par `select_round` plus haut
    # -- sans ce rechargement, `build_state_sync` lirait un `source_resolved_at`
    # perime porte par l'ancien objet Python, pas par la base. Un artefact du
    # cache ORM en memoire dans ce test, pas du produit : une requete HTTP/WS
    # reelle repart toujours d'un `Participant` frais.
    fac = Participant.objects.get(pk=fac.pk)
    payload = services.build_state_sync(fac)

    assert "chainingCandidates" not in payload
