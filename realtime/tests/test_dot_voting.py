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

Tache 6a-5 ajoute trois sections : `remaining_budget` (registre, sans DB),
`services.live_totals_payload`/`remaining_budgets` (domaine, avec DB mais
sans WebSocket -- la preuve par barriere reseau vit dans
`test_dot_voting_live.py`), et le test de concurrence du double-onglet
(deux vrais threads, voir sa docstring).
"""
from types import SimpleNamespace

import pytest
from django.db import connection

from decks.seed import create_dot_voting_deck, create_standard_deck
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


# --- Ce qu'il reste a placer : remaining_budget (design §4, tache 6a-5) ---


def test_remaining_budget_is_the_full_budget_when_nothing_was_placed_yet():
    # n=3 -> budget 6, rien pose encore.
    assert _spec().remaining_budget([], item_count=3) == 6


def test_remaining_budget_subtracts_what_was_already_placed_across_all_items():
    # n=3 -> budget 6. 2 (item 1) + 1 (item 2) deja poses -> reste 3.
    existing = [(1, {"points": 2}), (2, {"points": 1})]
    assert _spec().remaining_budget(existing, item_count=3) == 3


def test_remaining_budget_can_reach_zero_but_not_negative_by_construction():
    # n=2 -> budget 4, pile epuise.
    existing = [(1, {"points": 4})]
    assert _spec().remaining_budget(existing, item_count=2) == 0


def test_poker_declares_no_budget_notion():
    """Le poker (et toute activite sans hook propre) n'a aucune notion de
    budget -- `remaining_budget` reste `None`, `services.remaining_budgets`
    (teste plus bas) doit alors renvoyer `None` et ne rien diffuser."""
    assert spec_for("delegation_v1").remaining_budget is None
    assert spec_for("fist_of_five_v1").remaining_budget is None


# --- services.live_totals_payload : totaux en direct, gates sur la config --
#
# La regle du design §5 -- "le defaut est le secret" -- se verifie ici a
# l'echelle du DOMAINE (sans WebSocket). La preuve par barriere en
# aller-retour, seule valable pour prouver une ABSENCE cote reseau, est dans
# `test_dot_voting_live.py` (brief tache 6a-5, piege : "ce depot a deja vu un
# test d'absence passer sans rien verifier").


@pytest.mark.django_db
def test_live_totals_is_none_when_the_round_is_not_open():
    room, fac, voter, rnd, items = _dot_voting_room(2)
    # Round encore idle : meme si la config portait liveTotals, rien a
    # diffuser tant que personne ne peut voter.
    rnd.config = {"liveTotals": True}
    rnd.save(update_fields=["config"])
    assert services.live_totals_payload(room) is None


@pytest.mark.django_db
def test_live_totals_is_none_by_default_secret_config():
    """LE DEFAUT EST LE SECRET (design §5, derniere phrase) : une config
    absente (`Round.config == {}`, valeur par defaut du modele) n'est PAS un
    oubli traite comme un "oui" -- `cast_response` n'exige jamais que le
    facilitateur configure quoi que ce soit avant d'ouvrir le vote."""
    room, fac, voter, rnd, items = _dot_voting_room(2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    assert rnd.config == {}
    assert services.live_totals_payload(room) is None


@pytest.mark.django_db
def test_live_totals_is_none_when_the_config_explicitly_turns_it_off():
    room, fac, voter, rnd, items = _dot_voting_room(2)
    rnd.config = {"liveTotals": False}
    rnd.save(update_fields=["config"])
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    assert services.live_totals_payload(room) is None


@pytest.mark.django_db
def test_live_totals_carries_only_aggregates_when_the_config_allows_it():
    """Mode visible : le total EST diffusable, mais reste un AGREGAT --
    aucune cle nominative (`votes`, `participantId`) n'apparait jamais dans
    ce bloc, contrairement a `revealed_payload` (qui, lui, en porte une sur
    un round non anonyme apres reveal)."""
    room, fac, voter, rnd, items = _dot_voting_room(2)  # n=2 -> au plus 2 points par item
    rnd.config = {"liveTotals": True}
    rnd.save(update_fields=["config"])
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})

    totals = services.live_totals_payload(room)

    assert totals is not None
    blocks = {b["itemId"]: b for b in totals["itemResults"]}
    assert blocks[items[0].id]["totalPoints"] == 2
    assert blocks[items[0].id]["responseCount"] == 1
    assert blocks[items[1].id]["totalPoints"] == 0
    assert set(blocks[items[0].id].keys()) == {"itemId", "totalPoints", "responseCount"}


# --- services.remaining_budgets : au facilitateur seul, jamais au votant --


@pytest.mark.django_db
def test_remaining_budgets_is_none_for_an_activity_without_a_budget_notion():
    """Meme garde que `live_totals_payload`, mais pour le poker : aucune
    notion de budget declaree -- rien a diffuser (brief tache 6a-5)."""
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck))
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    rnd = Round.objects.create(room=room, facilitator=fac)
    Item.objects.create(round=rnd, text="Subject", sequence=1)
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    assert services.remaining_budgets(room) is None


@pytest.mark.django_db
def test_remaining_budgets_covers_every_participant_including_those_who_placed_nothing():
    room, fac, voter, rnd, items = _dot_voting_room(3)  # budget 6
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})

    budgets = services.remaining_budgets(room)

    assert budgets[str(voter.public_id)] == 4  # 6 - 2
    assert budgets[str(fac.public_id)] == 6  # rien pose : budget entier restant


# --- Le piege de la tache 3, corrige ici : le double-onglet -------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    not connection.features.has_select_for_update,
    reason=(
        "select_for_update() est un no-op silencieux sur ce moteur "
        "(SQLite, has_select_for_update=False) -- rien a demontrer ici, "
        "voir le commentaire de services._lock_participant_row. Ce test "
        "ne prouve la serialisation que sur un moteur qui verrouille "
        "reellement les lignes (PostgreSQL, la prod et la CI)."
    ),
)
def test_cast_response_serializes_two_concurrent_tabs_of_the_same_participant():
    """Piege releve tache 6a-3, corrige tache 6a-5, verrou deplace en base
    au round de correction 1 (le premier correctif, un verrou applicatif
    par processus, tenait a une topologie de deploiement plutot qu'a une
    regle produit -- voir le commentaire de `_lock_participant_row`) : lire
    le budget d'un participant PUIS l'ecrire n'etait pas serialise -- deux
    onglets du MEME participant, chacun declenchant son propre
    `cast_response()`, pouvaient tous deux lire le meme etat AVANT que l'un
    des deux n'ecrive, et depasser ensemble le budget de 2n.

    Reproduit une VRAIE concurrence, avec un VRAI verrou de base (pas un
    mock d'horloge, pas un verrou Python) : deux threads systeme reels,
    synchronises par des `threading.Event` autour du point ou
    `services._lock_participant_row` est appelee. Le premier thread
    l'appelle (ce qui emet le VRAI `SELECT ... FOR UPDATE`, a l'interieur
    de la transaction ouverte par `cast_response`), puis se met en pause
    -- transaction toujours ouverte, ligne toujours verrouillee en base --
    le temps que le second thread tente, lui aussi, d'appeler
    `_lock_participant_row` : son `SELECT ... FOR UPDATE` a lui bloque
    REELLEMENT dans PostgreSQL tant que la transaction du premier n'a pas
    commite (ou echoue), ce qui prouve qu'il en est bien BLOQUE au niveau
    du moteur, pas seulement qu'il s'execute apres par chance de
    l'ordonnanceur. Sans le verrou (mutation : `_lock_participant_row`
    neutralisee), le second thread ne serait jamais bloque : les deux
    liraient `existing` avant que l'un des deux n'ecrive, et la seconde
    reponse serait acceptee a tort.

    Mise en scene (n=3 -> budget 6, au plus 3 jetons par item -- deux items
    seuls, chacun a son maximum, ne peuvent jamais depasser 2*3=6 : la borne
    par item interdit mathematiquement de depasser le budget avec SEULEMENT
    deux ecritures concurrentes). Le voter pose D'ABORD, sequentiellement,
    3 points sur l'item 0 (moitie du budget, hors course) ; LA RACE porte sur
    les 3 points restants, chaque thread visant un item DIFFERENT (1 et 2)
    a 3 points chacun -- combines, 3 + 3 + 3 = 9 > 6."""
    import threading

    room, fac, voter, rnd, items = _dot_voting_room(3)  # n=3 -> budget 6
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 3})  # hors course : moitie du budget

    entered = threading.Event()
    release = threading.Event()
    real_lock_participant_row = services._lock_participant_row

    def instrumented(participant):
        real_lock_participant_row(participant)  # le VRAI SELECT ... FOR UPDATE
        if not entered.is_set():
            # Le PREMIER thread a l'appeler a deja acquis le VRAI verrou de
            # ligne (l'appel ci-dessus vient de le prouver) et se met en
            # pause ICI, la transaction toujours ouverte -- tant qu'il ne
            # l'a pas relachee, le second thread (ci-dessous) reste bloque
            # DANS LE MOTEUR sur son propre appel a `real_lock_participant_row`,
            # pas sur cet `Event`.
            entered.set()
            release.wait(timeout=2)

    services._lock_participant_row = instrumented
    try:
        results = {}

        def cast_first():
            try:
                services.cast_response(room, voter, items[1].id, {"points": 3})
                results["first"] = "ok"
            except Exception as exc:  # noqa: BLE001 -- capture pour assertion, pas pour avaler
                results["first"] = exc

        def cast_second():
            # Attend que le premier thread tienne deja le verrou avant de
            # tenter le sien -- sans cette attente, le second pourrait
            # s'executer avant meme que le premier ne l'ait pris, et le test
            # ne prouverait rien sur le verrou lui-meme.
            entered.wait(timeout=2)
            try:
                services.cast_response(room, voter, items[2].id, {"points": 3})
                results["second"] = "ok"
            except Exception as exc:  # noqa: BLE001
                results["second"] = exc

        t1 = threading.Thread(target=cast_first)
        t2 = threading.Thread(target=cast_second)
        t1.start()
        t2.start()
        # Laisse au second thread le temps d'atteindre son propre
        # `real_lock_participant_row(...)` et de s'y bloquer REELLEMENT (dans
        # le moteur) avant de liberer le premier -- une poignee de lectures
        # locales le separent de ce point, tres largement sous ce delai.
        import time

        time.sleep(0.2)
        release.set()
        t1.join(timeout=5)
        t2.join(timeout=5)
    finally:
        services._lock_participant_row = real_lock_participant_row

    assert results.get("first") == "ok"
    assert isinstance(results.get("second"), RoomError)
    assert results["second"].rejected_type == "response.cast"

    # Rien du second n'a ete ecrit sur son refus : la reponse hors-course
    # (item 0) et celle du premier thread (item 1) existent, exactement --
    # l'item 2, vise par le thread refuse, n'a RIEN.
    assert Response.objects.filter(round=rnd, participant=voter).count() == 2
    assert Response.objects.get(item=items[0], participant=voter).payload == {"points": 3}
    assert Response.objects.get(item=items[1], participant=voter).payload == {"points": 3}
    assert not Response.objects.filter(item=items[2], participant=voter).exists()


# --- state.sync : totaux en direct et reste a placer, a la reconnexion ---
#
# Round de correction 1 : `state.sync` ne rejoue AUCUN evenement (regle deja
# appliquee a `itemResults` -- test_state_sync_reveal.py -- et a
# `chainingCandidates` -- test_state_sync_chaining.py). Un facilitateur ou
# un votant qui recharge sa page en cours de round doit donc retrouver dans
# l'instantane tout ce que `response.totals`/`response.pending`
# (`realtime/consumers.py`) lui auraient deja appris -- aux MEMES
# conditions que ces diffusions, jamais des conditions relachees pour
# l'occasion.


@pytest.mark.django_db
def test_state_sync_carries_live_totals_when_the_config_allows_it():
    """A TOUT destinataire (ici le votant lui-meme) -- meme fonction,
    `live_totals_payload`, que celle qui alimente la diffusion : aucune
    divergence possible entre les deux chemins."""
    room, fac, voter, rnd, items = _dot_voting_room(2)
    rnd.config = {"liveTotals": True}
    rnd.save(update_fields=["config"])
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})

    payload = services.build_state_sync(voter)

    assert payload["liveTotals"] == services.live_totals_payload(room)
    blocks = {b["itemId"]: b for b in payload["liveTotals"]["itemResults"]}
    assert blocks[items[0].id]["totalPoints"] == 2


@pytest.mark.django_db
def test_state_sync_has_no_live_totals_in_secret_mode_by_default():
    room, fac, voter, rnd, items = _dot_voting_room(2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})

    payload = services.build_state_sync(voter)

    assert "liveTotals" not in payload


@pytest.mark.django_db
def test_state_sync_carries_pending_budgets_for_the_facilitator():
    room, fac, voter, rnd, items = _dot_voting_room(3)  # budget 6
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})

    payload = services.build_state_sync(fac)

    assert payload["pendingBudgets"][str(voter.public_id)] == 4  # 6 - 2
    assert payload["pendingBudgets"][str(fac.public_id)] == 6  # rien pose


@pytest.mark.django_db
def test_state_sync_never_carries_pending_budgets_for_a_voter():
    """Le cas qui compte : reserve au facilitateur, absente (pas vide) de
    l'etat d'un votant -- jamais une cle emise puis a masquer cote client."""
    room, fac, voter, rnd, items = _dot_voting_room(3)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})

    payload = services.build_state_sync(voter)

    assert "pendingBudgets" not in payload


@pytest.mark.django_db
def test_state_sync_has_no_pending_budgets_for_the_poker_facilitator():
    """Le poker n'a aucune notion de budget (`spec.remaining_budget is
    None`) -- `remaining_budgets` renvoie `None`, la cle reste absente meme
    pour le facilitateur."""
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck))
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    rnd = Round.objects.create(room=room, facilitator=fac)
    Item.objects.create(round=rnd, text="Subject", sequence=1)
    room.current_round = rnd
    room.save(update_fields=["current_round"])

    payload = services.build_state_sync(fac)

    assert "pendingBudgets" not in payload


# --- Performance du chemin chaud (round de correction 2, brief) ----------
#
# Chaque gommette posee declenche `live_totals_payload` : une requete PAR
# ITEM (`responses_of` appelee en boucle) aurait ete couteuse sur une salle
# a plusieurs items, sur une machine qui heberge dix applications Django et
# a deja sature une fois cette annee. Le nombre de requetes doit rester LE
# MEME, que le round porte 2 items ou 6 -- la preuve qu'aucune boucle
# n'interroge plus la base par item.


@pytest.mark.django_db
def test_live_totals_payload_does_not_query_once_per_item(django_assert_num_queries):
    room, fac, voter, rnd, items = _dot_voting_room(2)
    rnd.config = {"liveTotals": True}
    rnd.save(update_fields=["config"])
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 1})
    services.cast_response(room, voter, items[1].id, {"points": 1})

    with django_assert_num_queries(2):
        # 1 requete pour les items du round, 1 requete GROUPEE pour toutes
        # les reponses -- jamais une par item.
        services.live_totals_payload(room)

    room6, fac6, voter6, rnd6, items6 = _dot_voting_room(6)
    rnd6.config = {"liveTotals": True}
    rnd6.save(update_fields=["config"])
    services.open_vote(room6, fac6)
    for item in items6:
        services.cast_response(room6, voter6, item.id, {"points": 1})

    with django_assert_num_queries(2):
        # MEME nombre de requetes qu'avec 2 items : la preuve que le cout ne
        # grandit pas avec le nombre d'items du round.
        services.live_totals_payload(room6)
