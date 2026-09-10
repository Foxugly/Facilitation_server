"""L'ecart min/max n'a de sens que sur une echelle ordinale.

`delegation_v1` et `fist_of_five_v1` en ont une ; `roman_v1` non — ses valeurs
(+1/0/-1) sont des positions, pas des degres, et seul « 0 » passerait isdigit(),
ce qui afficherait un faux consensus.
"""
from realtime.services import _spread_for


def test_ordinal_strategies_get_a_spread():
    assert _spread_for("delegation_v1", ["1", "5", "3"]) == {"min": 1, "max": 5}
    assert _spread_for("fist_of_five_v1", ["0", "4"]) == {"min": 0, "max": 4}


def test_roman_vote_gets_no_spread():
    assert _spread_for("roman_v1", ["+1", "0", "-1"]) == {"min": None, "max": None}


def test_unknown_strategy_gets_no_spread():
    """Repli prudent : une strategie inconnue n'invente pas d'echelle."""
    assert _spread_for("something_new_v1", ["1", "2"]) == {"min": None, "max": None}


def test_ordinal_strategy_without_numeric_votes_gets_no_spread():
    assert _spread_for("delegation_v1", []) == {"min": None, "max": None}
