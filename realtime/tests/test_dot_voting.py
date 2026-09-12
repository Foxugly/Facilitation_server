"""Dot Voting, cote registre uniquement (design 2026-09-12, tache 6a-2).

Ce module teste `ACTIVITY_REGISTRY["dot_voting_v1"]` en isolation, exactement
comme `test_spread_strategy.py` le fait deja pour `delegation_v1`/
`fist_of_five_v1` : des `Response`/`Result` minimaux via `SimpleNamespace`,
jamais une DB. Rien ici ne passe par `cast_response` -- cette tache ne touche
pas `realtime/services.py` (brief tache 6a-2) : `item_count` n'est pas encore
branche sur le round reel, c'est l'ouverture de domaine que la tache suivante
fera (design section 3, point 3).
"""
from types import SimpleNamespace

import pytest

from realtime.activities import RoomError, spec_for, validate_config, validate_payload


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
    # autorise (design section 1 : "au plus n jetons" par item).
    assert _spec().validate_value({"points": 4}, [], item_count=4) is True


def test_zero_points_is_accepted():
    # Les jetons ne sont pas obligatoires (design section 4) : ne rien poser
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
