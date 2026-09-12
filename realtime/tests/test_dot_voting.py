"""Dot Voting, cote registre (design 2026-09-12, tache 6a-2) PUIS cote
`cast_response` (tache 6a-3).

Les tests du haut testent `ACTIVITY_REGISTRY["dot_voting_v1"]` en isolation,
exactement comme `test_spread_strategy.py` le fait deja pour `delegation_v1`/
`fist_of_five_v1` : des `Response`/`Result` minimaux via `SimpleNamespace`,
jamais une DB.

La section « Validation a l'echelle du round » (tache 6a-3) passe cette fois
par `services.cast_response`, avec une vraie DB (`create_dot_voting_deck` +
`Room`/`Round`/`Item` reels, exactement comme `test_responses_services.py`
le fait pour le poker) : c'est le seul moyen de verifier a la fois « rien
n'est ecrit sur un refus » et le piege du remplacement (design §3,
point 3 ; brief tache 6a-3).
"""
from types import SimpleNamespace

import pytest

from decks.seed import create_dot_voting_deck
from realtime import services
from realtime.activities import RoomError, spec_for, validate_config, validate_payload
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Response, Role, Room, Round
from rooms.snapshot import build_deck_snapshot


def _spec():
    return spec_for("dot_voting_v1")


def _response(points):
    return SimpleNamespace(payload={"points": points})


def _response_card(value):
    return SimpleNamespace(payload={"card": value})


def _result(chosen_value):
    return SimpleNamespace(chosen_value=chosen_value)


# --- Schema du payload -------------------------------------------------


def test_payload_schema_accepts_points_only():
    validate_payload("dot_voting_v1", {"points": 3})


def test_payload_schema_rejects_the_poker_shape():
    """Le meme payload que le poker (`{"card": ...}`) n'a plus la bonne
    forme pour dot_voting_v1 -- la cle attendue est `points`, pas `card`."""
    with pytest.raises(RoomError):
        validate_payload("dot_voting_v1", {"card": "3"})


# --- Valeur item par item : 0 <= points <= n ----------------------------


def test_a_value_within_bounds_is_accepted():
    # n = 4 items ; 3 jetons sur cet item est dans les bornes (0..4).
    assert _spec().validate_value({"points": 3}, [], item_count=4) is True


def test_the_upper_bound_itself_is_accepted():
    # La borne est INCLUSIVE : poser tous ses jetons sur un seul item est
    # autorise (design §1 : "au plus n jetons" par item).
    assert _spec().validate_value({"points": 4}, [], item_count=4) is True


def test_zero_points_is_accepted():
    # Les jetons ne sont pas obligatoires (design §4) : ne rien poser
    # sur un item est une valeur valide, pas une absence de reponse.
    assert _spec().validate_value({"points": 0}, [], item_count=4) is True


def test_a_value_beyond_the_bound_is_rejected():
    # n = 4 : 5 jetons sur un seul item depasse "au plus n".
    assert _spec().validate_value({"points": 5}, [], item_count=4) is False


def test_a_negative_value_is_rejected():
    assert _spec().validate_value({"points": -1}, [], item_count=4) is False


def test_a_non_integer_value_is_rejected():
    assert _spec().validate_value({"points": "3"}, [], item_count=4) is False


def test_a_boolean_value_is_rejected():
    # isinstance(True, int) vaut True en Python -- garde explicite, sinon un
    # payload {"points": true} passerait pour 1.
    assert _spec().validate_value({"points": True}, [], item_count=4) is False


def test_item_count_defaults_to_zero_when_not_supplied():
    """Defaut PRUDENT (voir le commentaire de `ActivitySpec.validate_value`,
    realtime/activities.py) : tant que `cast_response` ne fournit pas
    `item_count` (ouverture de domaine de la tache suivante), la borne haute
    retombe a 0 plutot que de laisser passer une valeur arbitraire."""
    assert _spec().validate_value({"points": 0}, []) is True
    assert _spec().validate_value({"points": 1}, []) is False


# --- Agregation : somme des points par item -----------------------------


def test_aggregate_sums_points_across_participants_for_one_item():
    responses = [_response(3), _response(5), _response(0)]
    counted = _spec().aggregate(responses, [])
    assert counted["totalPoints"] == 8
    assert counted["responseCount"] == 3


def test_aggregate_with_no_responses_yet_is_zero():
    counted = _spec().aggregate([], [])
    assert counted["totalPoints"] == 0
    assert counted["responseCount"] == 0


# --- Classement : rank_value, du plus haut au plus bas, ex aequo stables ---


def test_rank_value_reads_the_stored_total():
    assert _spec().rank_value(_result("12")) == 12


def test_ranking_orders_from_highest_to_lowest():
    results = [_result("5"), _result("9"), _result("1")]
    ranked = sorted(results, key=_spec().rank_value, reverse=True)
    assert [r.chosen_value for r in ranked] == ["9", "5", "1"]


def test_tied_items_keep_their_original_relative_order():
    """Departage deterministe (brief tache 6a-2) : `sorted(..., reverse=True)`
    est un tri STABLE en Python, donc deux items a egalite de points
    ressortent dans l'ordre ou ils ont ete presentes -- ici l'ordre de
    sequence du round source, exactement ce que fait deja
    `_chaining_candidate_items` (realtime/services.py) en triant par
    `(sequence, id)` avant d'appeler `rank_value`. Sans cette garantie,
    l'ordre d'un ex aequo pourrait changer d'un affichage a l'autre et
    rendrait l'historique du chainage incoherent."""
    first = _result("5")
    second = _result("9")
    third = _result("5")  # ex aequo avec `first`, mais arrive apres

    ranked = sorted([first, second, third], key=_spec().rank_value, reverse=True)

    assert ranked == [second, first, third]
    # Reordonner l'entree (meme multiset de valeurs) doit produire le meme
    # departage relatif : "first avant third" survit, quel que soit l'ordre
    # de depart, tant que leur ordre RELATIF entre eux ne change pas.
    ranked_again = sorted([third, first, second], key=_spec().rank_value, reverse=True)
    assert ranked_again == [second, third, first]


# --- Ce que l'activite consomme / produit, et la config qu'elle expose ----


def test_dot_voting_consumes_items_and_produces_rankable_results():
    spec = _spec()
    assert spec.consumes == "items"
    assert spec.produces == "results"
    assert spec.rank_value is not None


def test_config_schema_declares_the_live_totals_toggle():
    validate_config("dot_voting_v1", {"liveTotals": False})
    validate_config("dot_voting_v1", {"liveTotals": True})
    with pytest.raises(RoomError):
        validate_config("dot_voting_v1", {})
    with pytest.raises(RoomError):
        validate_config("dot_voting_v1", {"liveTotals": "yes"})


# --- Non-regression : le poker n'est pas affecte -------------------------


def test_poker_default_validate_value_still_works_with_two_arguments():
    """Le seul appel de production (`cast_response`, realtime/services.py)
    ne passe que 2 arguments positionnels a `validate_value` -- le
    troisieme (`item_count`) doit rester optionnel pour que ce site
    d'appel, non touche par cette tache, continue de fonctionner tel quel."""
    poker = spec_for("delegation_v1")
    assert poker.validate_value({"card": "5"}, ["1", "3", "5"]) is True
    assert poker.validate_value({"card": "9"}, ["1", "3", "5"]) is False


def test_poker_aggregate_and_ranking_are_unchanged():
    """Le poker garde son depouillement par defaut (tally + ecart ordinal)
    et ne declare toujours aucun classement -- dot_voting_v1 est une entree
    de PLUS dans le registre, pas une modification d'une entree existante."""
    poker = spec_for("delegation_v1")
    responses = [_response_card(v) for v in ("1", "3", "3")]
    counted = poker.aggregate(responses, ["1", "3", "5"])
    assert counted["tally"] == [{"cardValue": "1", "count": 1}, {"cardValue": "3", "count": 2}]
    assert counted["spread"] == {"min": 1, "max": 3}
    assert poker.rank_value is None


# --- Validation a l'echelle du round : validate_responses (tache 6a-3) ---
#
# La contrainte GLOBALE annoncee -- et deliberement pas verifiee -- par la
# tache 6a-2 (design §3, point 3) : la somme des jetons d'UN
# participant sur TOUT le round ne doit pas depasser son budget de 2n.
#
# Les tests d'abord au niveau du registre (rapides, sans DB, memes tuples
# `(item_id, payload)` que `validate_responses` recoit) pour epingler
# precisement le piege du remplacement ; puis a travers `cast_response`
# (avec une vraie DB) pour verifier ce que seul un aller-retour reel peut
# prouver : rien n'est ecrit sur un refus, et le type d'intention releve
# est bien celui que le client a emis.


def test_validate_responses_accepts_a_free_distribution_under_budget():
    # n=4 -> budget 8. 2 + 2 (deja poses) + 2 (tentative) = 6 <= 8.
    existing = [(1, {"points": 2}), (2, {"points": 2})]
    assert _spec().validate_responses(existing, 3, {"points": 2}, item_count=4) is True


def test_validate_responses_rejects_when_the_budget_is_exceeded():
    # n=4 -> budget 8. 4 + 4 (deja poses) + 1 (tentative) = 9 > 8.
    existing = [(1, {"points": 4}), (2, {"points": 4})]
    assert _spec().validate_responses(existing, 3, {"points": 1}, item_count=4) is False


def test_validate_responses_accepts_the_budget_exactly():
    existing = [(1, {"points": 4}), (2, {"points": 3})]
    assert _spec().validate_responses(existing, 3, {"points": 1}, item_count=4) is True


def test_validate_responses_does_not_double_count_a_replaced_item():
    """LE piege de la tache 6a-3 : corriger une reponse deja posee sur le
    MEME item ne doit pas compter l'ancienne valeur en plus de la nouvelle.

    n=4 -> budget 8. Le participant a deja pose 3 sur l'item 1 et 4 sur
    l'item 2 (total REEL deja pose : 7). Il corrige l'item 1 a 4 : l'etat
    APRES remplacement est 4 (item 1) + 4 (item 2) = 8, pile le budget --
    accepte.

    Une implementation qui additionnerait `existing` tel quel (3 + 4 = 7)
    et y ajouterait la tentative (4) obtiendrait 11 > 8 et refuserait a
    tort : c'est exactement le defaut que ce test epingle, verifie par
    mutation (voir rapport de tache -- neutraliser l'ecrasement par
    item_id dans `_dot_voting_validate_responses` fait echouer ce test)."""
    existing = [(1, {"points": 3}), (2, {"points": 4})]
    assert _spec().validate_responses(existing, 1, {"points": 4}, item_count=4) is True


def test_validate_responses_still_rejects_a_correction_that_truly_overspends():
    # Meme mise en scene, mais la correction porte l'item 1 a 5 : l'etat
    # APRES remplacement est 5 + 4 = 9 > 8 -- refuse, la aussi sans double
    # compte (5 + 4, pas 3 + 4 + 5).
    existing = [(1, {"points": 3}), (2, {"points": 4})]
    assert _spec().validate_responses(existing, 1, {"points": 5}, item_count=4) is False


def test_default_validate_responses_never_constrains_the_poker():
    """Le defaut (poker et toute activite sans hook propre) n'impose RIEN,
    quelle que soit la somme -- design §8, « pas de changement du poker »."""
    poker = spec_for("delegation_v1")
    existing = [(1, {"card": "5"}), (2, {"card": "5"}), (3, {"card": "5"})]
    assert poker.validate_responses(existing, 4, {"card": "5"}, item_count=1) is True


# --- Le meme piege, mais a travers cast_response (DB reelle) ------------


def _dot_voting_room(n_items):
    """Une room dot_voting_v1 avec un facilitateur, un votant, et un round
    courant portant `n_items` items -- meme forme que `_room()` dans
    `test_responses_services.py`, mais avec le deck sans carte de cette
    activite (design §7). `create_dot_voting_deck` seme `is_active
    =False` (tache 1) : sans consequence ici, ce drapeau ne filtre que le
    catalogue (`decks.selection`), jamais la construction directe d'une
    room ni `build_deck_snapshot`."""
    deck = create_dot_voting_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck))
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    rnd = Round.objects.create(room=room, facilitator=fac)
    items = [
        Item.objects.create(round=rnd, text=f"Item {i + 1}", sequence=i + 1) for i in range(n_items)
    ]
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    return room, fac, voter, rnd, items


@pytest.mark.django_db
def test_cast_response_refuses_over_budget_and_writes_nothing():
    # n=3 -> budget 6, borne par item = 3 (item_count).
    room, fac, voter, rnd, items = _dot_voting_room(3)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 3})
    services.cast_response(room, voter, items[1].id, {"points": 3})  # total = 6, pile le budget

    with pytest.raises(RoomError) as exc:
        services.cast_response(room, voter, items[2].id, {"points": 1})  # 6 + 1 > 6
    assert exc.value.rejected_type == "response.cast"

    # Rien n'est ecrit sur le refus : l'item 3 n'a toujours aucune reponse,
    # et les deux premieres n'ont pas bouge.
    assert not Response.objects.filter(item=items[2], participant=voter).exists()
    assert Response.objects.get(item=items[0], participant=voter).payload == {"points": 3}
    assert Response.objects.get(item=items[1], participant=voter).payload == {"points": 3}
    assert Response.objects.filter(round=rnd, participant=voter).count() == 2


@pytest.mark.django_db
def test_cast_response_allows_free_distribution_under_budget():
    # n=3 -> budget 6. 2 + 1 + 0 = 3 <= 6 : librement reparti, rien ne bloque.
    room, fac, voter, rnd, items = _dot_voting_room(3)
    services.open_vote(room, fac)

    services.cast_response(room, voter, items[0].id, {"points": 2})
    services.cast_response(room, voter, items[1].id, {"points": 1})
    services.cast_response(room, voter, items[2].id, {"points": 0})

    assert Response.objects.filter(round=rnd, participant=voter).count() == 3


@pytest.mark.django_db
def test_correcting_a_response_does_not_double_count_the_old_value_via_cast_response():
    """Le piege de la tache 6a-3, cette fois de bout en bout : `cast_response`
    REMPLACE via `update_or_create`, la validation de round doit donc juger
    l'etat APRES remplacement.

    n=4 -> budget 8. item1=3, item2=4 (total reel deja pose : 7). Corriger
    item1 a 4 : etat APRES remplacement = 4 + 4 = 8, pile le budget --
    DOIT etre accepte. Une implementation qui additionnerait l'ancien
    item1 (3) et le nouveau (4) verrait 3 + 4 + 4 = 11 > 8 et refuserait a
    tort la correction -- exactement ce qu'un facilitateur signalerait
    comme "je ne peux pas me corriger" (brief tache 6a-3)."""
    room, fac, voter, rnd, items = _dot_voting_room(4)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 3})
    services.cast_response(room, voter, items[1].id, {"points": 4})

    services.cast_response(room, voter, items[0].id, {"points": 4})  # correction, ne doit PAS lever

    assert Response.objects.get(item=items[0], participant=voter).payload == {"points": 4}
    assert Response.objects.get(item=items[1], participant=voter).payload == {"points": 4}
    assert Response.objects.filter(round=rnd, participant=voter).count() == 2


@pytest.mark.django_db
def test_a_correction_can_still_be_refused_if_it_truly_overspends():
    # Meme mise en scene que ci-dessus, mais la correction porte item1 a 5 :
    # etat APRES remplacement = 5 + 4 = 9 > 8 -- refuse, et rien n'est ecrit
    # (la valeur de item1 reste 3, celle d'avant la tentative refusee).
    room, fac, voter, rnd, items = _dot_voting_room(4)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 3})
    services.cast_response(room, voter, items[1].id, {"points": 4})

    with pytest.raises(RoomError) as exc:
        services.cast_response(room, voter, items[0].id, {"points": 5})
    assert exc.value.rejected_type == "response.cast"

    assert Response.objects.get(item=items[0], participant=voter).payload == {"points": 3}
    assert Response.objects.get(item=items[1], participant=voter).payload == {"points": 4}
