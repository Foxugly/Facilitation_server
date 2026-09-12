"""Depouillement et acte generiques, resultat fige (tache 6a-4).

Trois choses se jouent ici, et la premiere est la plus couteuse a rater :

1. **Le poker ne voit rien changer.** `revealed_payload` ecrivait
   `counted["tally"]` / `counted["spread"]` / `payload["card"]` en dur et
   `act_result` exigeait une carte du deck ; ces trois regles vivent
   desormais dans le registre, avec un defaut identique au code qu'elles
   remplacent. Verifie explicitement, pas par confiance : cles exactes du
   bloc de depouillement, `Result` ecrit a l'identique, historique relu de
   bout en bout par son vrai endpoint.
2. **Une activite non-poker se depouille et s'acte.** Les deux fonctions
   etaient l'une cassante (KeyError sur un agregat d'une autre forme) et
   l'autre INERTE (aucune valeur ne peut appartenir a un deck vide).
3. **Le resultat est fige a la revelation et ne se recalcule jamais.**
   Verifie par MUTATION : on change une reponse APRES la revelation et le
   classement ne bouge pas.
"""
import datetime
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from decks.seed import create_dot_voting_deck, create_standard_deck
from realtime import services
from realtime.activities import spec_for
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Response, Result, Role, Room, Round, RoundState
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


# --- Niveau registre, sans DB ------------------------------------------


def test_poker_declares_no_freeze_point():
    """La declaration qui garantit tout le reste : le poker ne fige rien a la
    revelation, donc `reveal` ne lui ecrit aucun `Result` et `act_result`
    continue d'ecrire le sien. Si cette assertion tombe, le poker a change de
    regime sans qu'on l'ait decide."""
    assert spec_for("delegation_v1").freeze_results is None
    assert spec_for("fist_of_five_v1").freeze_results is None


def test_default_response_view_is_the_poker_shape():
    """Le depouillement nominatif du poker emet `cardValue`, exactement comme
    le code en dur qu'il remplace."""
    assert spec_for("delegation_v1").response_view({"card": "5"}) == {"cardValue": "5"}


def test_default_validate_chosen_value_is_the_old_deck_membership_rule():
    poker = spec_for("delegation_v1")
    assert poker.validate_chosen_value("5", ["1", "5", "7"]) is True
    assert poker.validate_chosen_value("9", ["1", "5", "7"]) is False


def test_the_default_rule_refuses_everything_on_a_cardless_deck():
    """LA raison pour laquelle `act_result` etait inerte, epinglee : sur un
    deck sans carte (design section 7, `card_values` toujours vide), la regle
    par defaut refuse TOUTE valeur, y compris l'absence de valeur. Une
    activite sans cartes ne pouvait donc rien acter avant cette tache."""
    poker = spec_for("delegation_v1")
    assert poker.validate_chosen_value("3", []) is False
    assert poker.validate_chosen_value(None, []) is False
    assert poker.validate_chosen_value("", []) is False
    # ... et c'est bien le hook du registre qui debloque le cas.
    assert spec_for("dot_voting_v1").validate_chosen_value(None, []) is True


def test_dot_voting_freeze_ranks_by_total_descending():
    spec = spec_for("dot_voting_v1")
    aggregates = [
        (11, {"totalPoints": 2, "responseCount": 2}),
        (12, {"totalPoints": 7, "responseCount": 3}),
        (13, {"totalPoints": 5, "responseCount": 1}),
    ]
    frozen = spec.freeze_results(aggregates)
    assert [frozen[i]["payload"]["rank"] for i in (12, 13, 11)] == [1, 2, 3]
    # `chosenValue` porte le total, en chaine : c'est le champ que relit le
    # chainage « top N » (`rank_value`).
    assert frozen[12]["chosenValue"] == "7"
    # Le payload garde l'agregat tel quel, plus le rang -- meme forme que ce
    # que `revealed_payload` aurait diffuse en recalculant.
    assert frozen[12]["payload"] == {"totalPoints": 7, "responseCount": 3, "rank": 1}


def test_dot_voting_freeze_breaks_ties_by_item_sequence():
    """Le departage choisi par la tache 6a-2 et repris ici, PAS un second :
    `aggregates` arrive dans l'ordre (sequence, id) et `sorted` est stable,
    donc a egalite de points l'item de plus petite sequence passe devant."""
    spec = spec_for("dot_voting_v1")
    aggregates = [
        (21, {"totalPoints": 4, "responseCount": 2}),
        (22, {"totalPoints": 4, "responseCount": 2}),
        (23, {"totalPoints": 4, "responseCount": 2}),
    ]
    frozen = spec.freeze_results(aggregates)
    assert [frozen[i]["payload"]["rank"] for i in (21, 22, 23)] == [1, 2, 3]


def test_frozen_chosen_value_feeds_rank_value():
    """Le fil complet du chainage « top N » : ce que `freeze_results` ecrit
    dans `chosen_value` est exactement ce que `rank_value` sait relire."""
    spec = spec_for("dot_voting_v1")
    frozen = spec.freeze_results([(31, {"totalPoints": 6, "responseCount": 2})])
    result = SimpleNamespace(chosen_value=frozen[31]["chosenValue"])
    assert spec.rank_value(result) == 6


# --- Rooms de test ------------------------------------------------------


def _poker_room(team=None):
    """Une room delegation-poker, meme forme que `_room()` dans
    `test_responses_services.py`, avec une equipe optionnelle pour que
    l'historique (reserve aux rooms d'equipe) puisse la relire."""
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, team=team, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck))
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    rnd = Round.objects.create(room=room, facilitator=fac)
    item = Item.objects.create(round=rnd, text="Budget?", sequence=1)
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    return room, fac, voter, rnd, item


def _dot_voting_room(n_items):
    """Meme forme, avec le deck SANS CARTE de dot_voting (design section 7)."""
    deck = create_dot_voting_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck))
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    rnd = Round.objects.create(room=room, facilitator=fac)
    items = [Item.objects.create(round=rnd, text=f"Item {i + 1}", sequence=i + 1) for i in range(n_items)]
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    return room, fac, voter, rnd, items


# --- Non-regression du poker -------------------------------------------


@pytest.mark.django_db
def test_poker_revealed_payload_keys_are_unchanged():
    """Le bloc de depouillement du poker, cle par cle. `revealed_payload`
    fusionne desormais l'agregat (`**counted`) au lieu d'y piocher `tally` et
    `spread` : si cette generalisation avait change la moindre cle, le
    frontend perdrait son tapis."""
    room, fac, voter, rnd, item = _poker_room()
    services.open_vote(room, fac)
    services.cast_response(room, voter, item.id, {"card": "5"})
    services.cast_response(room, fac, item.id, {"card": "3"})
    services.reveal(room, fac)

    payload = services.revealed_payload(room)
    assert set(payload.keys()) == {"itemResults", "anonymous"}
    block = payload["itemResults"][0]
    assert set(block.keys()) == {"itemId", "tally", "spread", "anonymous", "votes"}
    assert block["tally"] == [{"cardValue": "3", "count": 1}, {"cardValue": "5", "count": 1}]
    assert block["spread"] == {"min": 3, "max": 5}
    assert {v["cardValue"] for v in block["votes"]} == {"3", "5"}
    assert set(block["votes"][0].keys()) == {"participantId", "cardValue"}


@pytest.mark.django_db
def test_poker_reveal_writes_no_result_and_act_writes_it_exactly_as_before():
    """Le poker ne fige rien a la revelation (aucun `Result`), puis
    `act_result` ecrit le sien a l'identique : meme `chosen_value`, meme
    `decided_by`, et le champ `payload` ajoute par cette tache reste vide --
    c'est ce que « additif » veut dire."""
    room, fac, voter, rnd, item = _poker_room()
    services.open_vote(room, fac)
    services.cast_response(room, voter, item.id, {"card": "5"})
    services.reveal(room, fac)

    assert Result.objects.filter(round=rnd).count() == 0

    services.act_result(room, fac, "5")

    result = Result.objects.get(round=rnd)
    assert result.chosen_value == "5"
    assert result.item_id == item.id
    assert result.decided_by_id == fac.id
    assert result.payload == {}
    rnd.refresh_from_db()
    assert rnd.state == RoundState.ACTED


@pytest.mark.django_db
def test_poker_act_still_refuses_a_value_outside_the_deck():
    room, fac, voter, rnd, item = _poker_room()
    services.open_vote(room, fac)
    services.cast_response(room, voter, item.id, {"card": "5"})
    services.reveal(room, fac)

    with pytest.raises(RoomError) as exc:
        services.act_result(room, fac, "42")
    assert exc.value.rejected_type == "result.act"
    assert not Result.objects.filter(round=rnd).exists()


@pytest.mark.django_db
def test_history_still_reads_a_poker_result_unchanged():
    """L'historique de bout en bout, par son vrai endpoint : il lit
    `Result.chosen_value` et le traduit via le snapshot de deck. Le champ
    `payload` ajoute par cette tache ne le concerne pas et ne doit rien y
    changer."""
    user = User.objects.create_user(email="hist@example.com", password="pw12345678", display_name="Mia")
    team = Team.objects.create(name="Squad", owner=user)
    TeamMembership.objects.create(team=team, user=user, role=TeamRole.OWNER)
    room, fac, voter, rnd, item = _poker_room(team=team)
    services.open_vote(room, fac)
    services.cast_response(room, voter, item.id, {"card": "5"})
    services.reveal(room, fac)
    services.act_result(room, fac, "5")

    client = APIClient()
    client.force_authenticate(user)
    day = datetime.date.today().isoformat()
    detail = client.get(f"/api/v1/history/{team.id}/{day}/").json()
    entry = detail["entries"][0]
    assert entry["subject"] == "Budget?"
    assert entry["chosenValue"] == "5"
    assert entry["levelName"]["fr"] == "Conseiller"


# --- Une activite sans cartes : depouillement et acte -------------------


@pytest.mark.django_db
def test_dot_voting_reveal_no_longer_raises_and_carries_its_own_shape():
    """Le depouillement d'une activite non-poker. Avant cette tache,
    `revealed_payload` levait une KeyError immediate sur `counted["tally"]`,
    absent de l'agregat de dot voting."""
    room, fac, voter, rnd, items = _dot_voting_room(3)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 1})
    services.cast_response(room, voter, items[1].id, {"points": 3})
    services.cast_response(room, fac, items[1].id, {"points": 2})
    services.reveal(room, fac)

    blocks = {b["itemId"]: b for b in services.revealed_payload(room)["itemResults"]}
    assert set(blocks[items[1].id].keys()) == {
        "itemId", "totalPoints", "responseCount", "rank", "anonymous", "votes",
    }
    assert blocks[items[1].id]["totalPoints"] == 5
    assert blocks[items[0].id]["totalPoints"] == 1
    assert blocks[items[2].id]["totalPoints"] == 0
    # Le classement : item2 (5) devant item1 (1) devant item3 (0).
    assert [blocks[i.id]["rank"] for i in items] == [2, 1, 3]
    # Le depouillement nominatif dit des POINTS, pas une carte absente.
    assert set(blocks[items[1].id]["votes"][0].keys()) == {"participantId", "points"}
    assert sorted(v["points"] for v in blocks[items[1].id]["votes"]) == [2, 3]


@pytest.mark.django_db
def test_dot_voting_anonymous_reveal_emits_no_individual_values():
    """L'invariant de secret tient PAR CONSTRUCTION, y compris sur la
    nouvelle activite : sur un round anonyme aucun bloc ne porte `votes`.
    Le serveur ne construit pas les valeurs individuelles, il ne les masque
    pas."""
    room, fac, voter, rnd, items = _dot_voting_room(2)
    # Pose directement : `set_reveal_mode` exige une equipe payante, hors sujet
    # ici -- ce qui est teste, c'est ce que `revealed_payload` CONSTRUIT.
    rnd.is_anonymous = True
    rnd.save(update_fields=["is_anonymous"])
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    services.reveal(room, fac)

    for block in services.revealed_payload(room)["itemResults"]:
        assert "votes" not in block
        assert block["anonymous"] is True


@pytest.mark.django_db
def test_dot_voting_can_act_its_result_which_was_impossible():
    """L'acte d'une activite SANS cartes. Avant cette tache la garde
    `chosen_value not in _card_values(room)` refusait tout, le deck etant
    vide : la fonction n'etait pas « pas encore branchee », elle etait
    inerte."""
    room, fac, voter, rnd, items = _dot_voting_room(2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    services.cast_response(room, voter, items[1].id, {"points": 1})
    services.reveal(room, fac)

    services.act_result(room, fac, None)

    rnd.refresh_from_db()
    assert rnd.state == RoundState.ACTED
    # L'acte n'a RIEN reecrit : les `Result` sont ceux que la revelation a
    # figes, un par item, avec leur classement.
    results = {r.item_id: r for r in Result.objects.filter(round=rnd)}
    assert set(results) == {items[0].id, items[1].id}
    assert results[items[0].id].chosen_value == "2"
    assert results[items[0].id].payload["rank"] == 1
    assert results[items[1].id].payload["rank"] == 2


@pytest.mark.django_db
def test_dot_voting_act_refuses_a_value_since_the_ranking_is_already_frozen():
    room, fac, voter, rnd, items = _dot_voting_room(2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    services.reveal(room, fac)

    with pytest.raises(RoomError) as exc:
        services.act_result(room, fac, "2")
    assert exc.value.rejected_type == "result.act"
    rnd.refresh_from_db()
    assert rnd.state == RoundState.REVEALED


# --- Le figement, verifie par mutation ----------------------------------


@pytest.mark.django_db
def test_a_revealed_ranking_does_not_move_when_a_response_changes_afterwards():
    """L'invariant du figement, la raison d'etre de `Result.payload` : le
    classement devient l'entree d'une autre activite (chainage « top N ») et
    l'historique doit rester stable, donc il ne se recalcule jamais.

    Mutation : on reecrit une `Response` EN BASE apres la revelation -- ce
    qu'aucun chemin de production ne fait aujourd'hui, mais qui est
    exactement la classe de bug que le figement doit rendre impossible
    (reprise, rejeu, ecriture concurrente en vol). Sans figement, le
    depouillement suivrait la mutation : item2 passerait devant item1 et le
    classement change sous les yeux de la salle."""
    room, fac, voter, rnd, items = _dot_voting_room(2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    services.cast_response(room, voter, items[1].id, {"points": 1})
    services.reveal(room, fac)

    before = {b["itemId"]: (b["totalPoints"], b["rank"]) for b in services.revealed_payload(room)["itemResults"]}
    assert before == {items[0].id: (2, 1), items[1].id: (1, 2)}

    # LA mutation : item2 passe a 9 points, apres la revelation.
    Response.objects.filter(round=rnd, item=items[1], participant=voter).update(payload={"points": 9})

    after = {b["itemId"]: (b["totalPoints"], b["rank"]) for b in services.revealed_payload(room)["itemResults"]}
    assert after == before
    # Et la source de verite, en base, n'a pas bouge non plus.
    assert Result.objects.get(round=rnd, item=items[1]).chosen_value == "1"


@pytest.mark.django_db
def test_the_timer_reveal_freezes_the_ranking_too():
    """Une revelation par echeance produit le MEME resultat stable qu'une
    revelation manuelle : sans cela, un round revele par le timer resterait
    recalcule a chaque lecture."""
    room, fac, voter, rnd, items = _dot_voting_room(2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    # Echeance deja depassee : `reveal_on_timeout` doit reveler ET figer.
    Round.objects.filter(pk=rnd.pk).update(
        vote_deadline=services.timezone.now() - datetime.timedelta(seconds=1)
    )
    room.current_round.refresh_from_db()

    assert services.reveal_on_timeout(room) is True
    assert Result.objects.get(round=rnd, item=items[0]).chosen_value == "2"


@pytest.mark.django_db
def test_replaying_a_reset_round_refreezes_instead_of_failing():
    """`vote.reset` remet le round a IDLE en LAISSANT son `Result` en place
    (comportement documente de `reset_round`). Rejouer puis reveler a nouveau
    doit donc REFIGER, pas violer la contrainte d'unicite (round, item)."""
    room, fac, voter, rnd, items = _dot_voting_room(2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 2})
    services.reveal(room, fac)
    assert Result.objects.get(round=rnd, item=items[0]).chosen_value == "2"

    services.reset_round(room, fac)
    services.open_vote(room, fac)
    services.cast_response(room, voter, items[0].id, {"points": 1})
    services.reveal(room, fac)

    assert Result.objects.filter(round=rnd, item=items[0]).count() == 1
    assert Result.objects.get(round=rnd, item=items[0]).chosen_value == "1"
