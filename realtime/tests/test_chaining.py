"""Chainage entre deux rounds (design 2026-09-11 section7, tache 1 de la
livraison 5e).

Cette tache pose la liaison sans la resoudre : `Round.source_round` /
`Round.source_rule`, et ce que le registre declare consommer/produire. Ni
copie, ni validation de regle -- la tache suivante.
"""
import pytest

from realtime.activities import DEFAULT_SPEC, spec_for
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, Round

pytestmark = pytest.mark.django_db


@pytest.fixture
def room_with_facilitator(standard_deck):
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
    return room, fac


def test_source_round_and_source_rule_round_trip_through_the_database(room_with_facilitator):
    """Les deux champs existent, acceptent une valeur concrete et la
    retrouvent intacte apres un rechargement depuis la base."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    rule = {"take": "results", "mode": "manual", "top": 3}
    consumer = Round.objects.create(
        room=room, facilitator=fac, sequence=2, source_round=source, source_rule=rule
    )

    reloaded = Round.objects.get(pk=consumer.pk)
    assert reloaded.source_round_id == source.pk
    assert reloaded.source_rule == {"take": "results", "mode": "manual", "top": 3}


def test_deleting_the_source_round_does_not_delete_the_consumer_round(room_with_facilitator):
    """La copie appartient deja au round consommateur : supprimer la source
    (elaguer le scenario) ne doit pas l'emporter, sans quoi on detruirait un
    round DEJA JOUE. SET_NULL, pas CASCADE : le lien se detache, le round
    consommateur survit."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    consumer = Round.objects.create(
        room=room, facilitator=fac, sequence=2,
        source_round=source, source_rule={"take": "items", "mode": "auto", "top": None},
    )

    source.delete()

    reloaded = Round.objects.get(pk=consumer.pk)
    assert reloaded.source_round_id is None
    # La regle de selection, elle, ne decoulait pas de l'existence de la
    # source : elle reste lisible pour l'historique / le debug.
    assert reloaded.source_rule == {"take": "items", "mode": "auto", "top": None}


def test_registry_declares_what_poker_consumes_and_produces():
    """Le poker consomme des items saisis et produit un resultat fige par
    item : c'est ce qui le rend chainable en amont d'une autre activite."""
    spec = spec_for("delegation_v1")
    assert spec.consumes == "items"
    assert spec.produces == "results"


def test_an_unknown_strategy_falls_back_to_a_cautious_default():
    """Une strategie absente du registre ne doit pas se pretendre
    consommatrice ou productrice de quoi que ce soit : le defaut prudent est
    "none" des deux cotes, comme `DEFAULT_SPEC`."""
    spec = spec_for("une_strategie_qui_n_existe_pas")
    assert spec is DEFAULT_SPEC
    assert spec.consumes == "none"
    assert spec.produces == "none"
