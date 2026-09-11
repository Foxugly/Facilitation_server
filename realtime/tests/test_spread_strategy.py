"""L'ecart min/max n'a de sens que sur une echelle ordinale.

`delegation_v1` et `fist_of_five_v1` en ont une ; `roman_v1` non — ses valeurs
(+1/0/-1) sont des positions, pas des degres, et seul « 0 » passerait isdigit(),
ce qui afficherait un faux consensus.

Round de correction 1 (tache 3) : `services._spread_for` et
`activities.is_ordinal` ont ete supprimes — la regle ordinale ne vivait deja
plus qu'en apparence a deux endroits, `_default_aggregate` la reimplementant
sans que rien ne les maintienne synchronises. Ces cas restent ceux qui
comptent ; leur cible devient l'agregateur du registre lui-meme
(`ActivitySpec.aggregate`), le seul endroit ou la regle vit desormais.
"""
from types import SimpleNamespace

from realtime.activities import spec_for


def _spread(strategy, card_values):
    """Reconstruit des Response minimales (seul `card_value` compte pour
    l'agregateur) et lit `spread` dans le resultat de l'agregateur de la
    strategie — exactement ce que `revealed_payload` fait en production."""
    responses = [SimpleNamespace(card_value=v) for v in card_values]
    return spec_for(strategy).aggregate(responses, card_values)["spread"]


def test_ordinal_strategies_get_a_spread():
    assert _spread("delegation_v1", ["1", "5", "3"]) == {"min": 1, "max": 5}
    assert _spread("fist_of_five_v1", ["0", "4"]) == {"min": 0, "max": 4}


def test_roman_vote_gets_no_spread():
    assert _spread("roman_v1", ["+1", "0", "-1"]) == {"min": None, "max": None}


def test_unknown_strategy_gets_no_spread():
    """Repli prudent : une strategie inconnue n'invente pas d'echelle."""
    assert _spread("something_new_v1", ["1", "2"]) == {"min": None, "max": None}


def test_ordinal_strategy_without_numeric_votes_gets_no_spread():
    assert _spread("delegation_v1", []) == {"min": None, "max": None}
