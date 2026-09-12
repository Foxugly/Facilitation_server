"""Chainage entre deux rounds (design 2026-09-11 §7).

Tache 1 (ci-dessus) a pose la liaison sans la resoudre. Tache 2 (ci-dessous)
la resout en copie : `bind_round` declare, `resolve_source` copie -- en auto
au demarrage du round consommateur (via `select_round`), en manuel a la
validation explicite du facilitateur -- et `chaining_candidates` presente ce
qu'un mode manuel a le droit de cocher.
"""
import pytest

from realtime.activities import ACTIVITY_REGISTRY, ActivitySpec, DEFAULT_SPEC, spec_for
from realtime.services import (
    RoomError,
    bind_round,
    chaining_candidates,
    resolve_source,
    select_round,
    update_item,
)
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Result, Role, Room, Round, RoundState

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


# ---------------------------------------------------------------------------
# Tache 2 -- resoudre la liaison en copie
# ---------------------------------------------------------------------------
#
# Sauf mention contraire, `source` et `consumer` heritent de
# `room.deck_snapshot` (`standard_deck` -> strategie "delegation_v1",
# consumes="items", produces="results") : aucun des deux n'a besoin d'un
# `deck_snapshot` propre pour les scenarios "items" les plus simples.


def _auto_rule(take="items", top=None):
    return {"take": take, "mode": "auto", "top": top}


def _manual_rule(take="items", top=None):
    return {"take": take, "mode": "manual", "top": top}


def test_auto_chaining_copies_source_items_when_the_consumer_round_becomes_current(
    room_with_facilitator,
):
    """Comportement 1 du brief : une liaison auto copie au demarrage du round
    consommateur (`select_round`), les copies portent `origin_item`, et les
    items de la source ne bougent pas (ni texte, ni compte)."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    a = Item.objects.create(round=source, text="Post-it A", sequence=1)
    b = Item.objects.create(round=source, text="Post-it B", sequence=2)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    bind_round(room, fac, consumer.id, source.id, _auto_rule())
    select_round(room, fac, consumer.id)

    copied = list(consumer.items.order_by("sequence"))
    assert [item.text for item in copied] == ["Post-it A", "Post-it B"]
    assert [item.origin_item_id for item in copied] == [a.id, b.id]
    # La source n'a pas bouge : ni le texte, ni le nombre d'items.
    assert [item.text for item in source.items.order_by("sequence")] == ["Post-it A", "Post-it B"]
    assert source.items.count() == 2


def test_chaining_from_a_still_open_source_round_is_allowed(room_with_facilitator):
    """Design §7 : « source encore ouverte : autorisee (copie de l'etat a
    l'instant T) ». La source ne le sait meme pas -- son etat ne change pas."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1, state=RoundState.OPEN)
    Item.objects.create(round=source, text="En cours", sequence=1)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    bind_round(room, fac, consumer.id, source.id, _auto_rule())
    select_round(room, fac, consumer.id)

    assert consumer.items.count() == 1
    source.refresh_from_db()
    assert source.state == RoundState.OPEN


def test_manual_chaining_copies_nothing_until_the_facilitator_validates(room_with_facilitator):
    """Comportement 2 (premiere moitie) : un round manuel devenu courant ne
    copie rien tout seul."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    Item.objects.create(round=source, text="Garder", sequence=1)
    Item.objects.create(round=source, text="Ecarter", sequence=2)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    bind_round(room, fac, consumer.id, source.id, _manual_rule())
    select_round(room, fac, consumer.id)

    assert consumer.items.count() == 0


def test_manual_chaining_validation_copies_exactly_what_was_checked(room_with_facilitator):
    """Comportement 2 (seconde moitie) : la validation manuelle copie
    EXACTEMENT la selection du facilitateur, ni plus ni moins."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    keep = Item.objects.create(round=source, text="Garder", sequence=1)
    Item.objects.create(round=source, text="Ecarter", sequence=2)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    bind_round(room, fac, consumer.id, source.id, _manual_rule())
    select_round(room, fac, consumer.id)

    resolve_source(room, fac, consumer.id, item_ids=[keep.id])

    copied = list(consumer.items.all())
    assert [item.text for item in copied] == ["Garder"]
    assert copied[0].origin_item_id == keep.id


def test_chaining_candidates_lists_what_a_manual_facilitator_may_check(room_with_facilitator):
    """`chaining_candidates` est ce que le front afficherait a cocher : les
    items de la source, dans l'ordre du round."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    a = Item.objects.create(round=source, text="Un", sequence=1)
    b = Item.objects.create(round=source, text="Deux", sequence=2)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)
    bind_round(room, fac, consumer.id, source.id, _manual_rule())

    candidates = chaining_candidates(room, consumer.id)

    assert [c["itemId"] for c in candidates] == [a.id, b.id]
    assert [c["text"] for c in candidates] == ["Un", "Deux"]


def test_top_rule_keeps_only_the_ranked_top_n(room_with_facilitator, monkeypatch):
    """Comportement 3 (moitie positive) : une source dont l'agregateur
    produit un classement ne reprend, en top N, que les N premiers."""
    room, fac = room_with_facilitator
    monkeypatch.setitem(
        ACTIVITY_REGISTRY,
        "ranked_v1",
        ActivitySpec(
            consumes="items", produces="results", rank_value=lambda result: int(result.chosen_value)
        ),
    )
    source = Round.objects.create(
        room=room, facilitator=fac, sequence=1,
        deck_snapshot={"resolutionStrategy": "ranked_v1", "cards": []},
    )
    items = [
        Item.objects.create(round=source, text=text, sequence=n)
        for n, text in enumerate(("Basse", "Haute", "Tres basse"), start=1)
    ]
    for item, value in zip(items, ("5", "9", "1")):
        Result.objects.create(round=source, item=item, chosen_value=value)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    bind_round(room, fac, consumer.id, source.id, _auto_rule(take="results", top=2))
    select_round(room, fac, consumer.id)

    # Classees par valeur decroissante (9, 5, 1) : seules les 2 premieres passent.
    assert [item.text for item in consumer.items.order_by("sequence")] == ["Haute", "Basse"]


def test_top_rule_is_refused_at_declaration_when_the_source_has_no_ranking(room_with_facilitator):
    """Comportement 3 (moitie negative) : `delegation_v1` produit des
    `Result`, mais ne declare aucun `rank_value` -- un `top` sur cette source
    doit etre refuse a `bind_round`, pas accepte puis ignore au demarrage."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    item = Item.objects.create(round=source, text="Seul item", sequence=1)
    Result.objects.create(round=source, item=item, chosen_value="4")
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    with pytest.raises(RoomError):
        bind_round(room, fac, consumer.id, source.id, _auto_rule(take="results", top=1))

    consumer.refresh_from_db()
    assert consumer.source_round_id is None
    assert consumer.source_rule is None


def test_bind_refuses_when_the_target_activity_does_not_consume_items(room_with_facilitator):
    """Comportement 4 (moitie cible) : une cible dont le registre declare
    `consumes="none"` ne peut pas recevoir de chainage."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    Item.objects.create(round=source, text="Un sujet", sequence=1)
    consumer = Round.objects.create(
        room=room, facilitator=fac, sequence=2,
        deck_snapshot={"resolutionStrategy": "une_strategie_inconnue", "cards": []},
    )

    with pytest.raises(RoomError):
        bind_round(room, fac, consumer.id, source.id, _auto_rule())

    consumer.refresh_from_db()
    assert consumer.source_round_id is None


def test_bind_refuses_taking_results_from_a_source_that_produces_none(room_with_facilitator):
    """Comportement 4 (moitie source) : `take: "results"` exige que la
    source en produise -- une strategie inconnue (`produces="none"`) n'en a
    aucun a offrir."""
    room, fac = room_with_facilitator
    source = Round.objects.create(
        room=room, facilitator=fac, sequence=1,
        deck_snapshot={"resolutionStrategy": "une_strategie_inconnue", "cards": []},
    )
    Item.objects.create(round=source, text="Un sujet", sequence=1)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    with pytest.raises(RoomError):
        bind_round(room, fac, consumer.id, source.id, _auto_rule(take="results"))

    consumer.refresh_from_db()
    assert consumer.source_round_id is None


def test_reformulating_a_copied_item_does_not_change_the_source_item(room_with_facilitator):
    """Comportement 5, l'invariant central de §7 : une copie reste une copie.
    Verifie par mutation -- voir task-2-report.md pour la trace."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    original = Item.objects.create(round=source, text="Texte original", sequence=1)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)
    bind_round(room, fac, consumer.id, source.id, _auto_rule())
    select_round(room, fac, consumer.id)

    copy = consumer.items.get(origin_item=original)
    update_item(room, fac, copy.id, "Texte reformule")

    original.refresh_from_db()
    copy.refresh_from_db()
    assert original.text == "Texte original"
    assert copy.text == "Texte reformule"


def test_copied_item_keeps_its_original_author(room_with_facilitator):
    """Comportement 6 : un post-it ne perd pas son auteur en changeant
    d'activite."""
    room, fac = room_with_facilitator
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Lou")
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    post_it = Item.objects.create(round=source, text="Idee de Lou", sequence=1, author=voter)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)
    bind_round(room, fac, consumer.id, source.id, _auto_rule())
    select_round(room, fac, consumer.id)

    copy = consumer.items.get(origin_item=post_it)
    assert copy.author_id == voter.id


def test_resolving_the_same_binding_twice_does_not_duplicate_items(room_with_facilitator):
    """Comportement 7 : le round consommateur redevient courant (reouvert
    puis re-selectionne) sans jamais dupliquer sa copie."""
    room, fac = room_with_facilitator
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    Item.objects.create(round=source, text="Unique", sequence=1)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)
    bind_round(room, fac, consumer.id, source.id, _auto_rule())

    select_round(room, fac, consumer.id)
    assert consumer.items.count() == 1

    # Rappel direct (idempotence de `resolve_source` lui-meme)...
    resolve_source(room, fac, consumer.id)
    # ... puis via le chemin complet : le round redevient courant une seconde fois.
    select_round(room, fac, consumer.id)

    assert consumer.items.count() == 1


def test_bind_round_is_reserved_to_the_facilitator(room_with_facilitator):
    """Comportement 6 du brief (« le serveur fait autorite ») : un participant
    ordinaire ne peut pas declarer de liaison."""
    room, fac = room_with_facilitator
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Lou")
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)

    with pytest.raises(RoomError) as excinfo:
        bind_round(room, voter, consumer.id, source.id, _auto_rule())
    assert excinfo.value.code == "forbidden.not_facilitator"

    consumer.refresh_from_db()
    assert consumer.source_round_id is None


def test_resolve_source_is_reserved_to_the_facilitator(room_with_facilitator):
    """Meme garde que `bind_round`, sur `resolve_source`."""
    room, fac = room_with_facilitator
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Lou")
    source = Round.objects.create(room=room, facilitator=fac, sequence=1)
    Item.objects.create(round=source, text="Un sujet", sequence=1)
    consumer = Round.objects.create(room=room, facilitator=fac, sequence=2)
    bind_round(room, fac, consumer.id, source.id, _auto_rule())

    with pytest.raises(RoomError) as excinfo:
        resolve_source(room, voter, consumer.id)
    assert excinfo.value.code == "forbidden.not_facilitator"
