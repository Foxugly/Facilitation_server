"""Le domaine agrege par item, le registre declare le schema (tache 3).

`cast_response` cible desormais un ITEM et non plus un round : un round peut
porter plusieurs items, chacun avec sa propre Response par participant
(contrainte `uniq_response_item_participant`). `revealed_payload` porte un
bloc par item ; les cles plates historiques (`tally`/`spread`/`votes`) et la
facade `cast_vote` ont ete retirees en fin de 5b (contrat §8.2.b).
"""
import pytest

from decks.seed import create_standard_deck
from realtime import services
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Response, Role, Room, Round
from rooms.snapshot import build_deck_snapshot


def _room():
    """Une room delegation-poker avec un facilitateur, un votant, et un round
    courant portant un seul item — le point de depart de chaque test, comme
    `test_reveal_mode.py`."""
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck))
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    rnd = Round.objects.create(room=room, facilitator=fac)
    item = Item.objects.create(round=rnd, text="Deploys", sequence=1)
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    return room, fac, voter, rnd, item


@pytest.mark.django_db
def test_cast_response_writes_the_targeted_item_and_refuses_a_foreign_item():
    room, fac, voter, rnd, item = _room()
    other_rnd = Round.objects.create(room=room, facilitator=fac)
    other_item = Item.objects.create(round=other_rnd, text="Autre round", sequence=1)
    services.open_vote(room, fac)

    with pytest.raises(RoomError) as exc:
        services.cast_response(room, voter, other_item.id, {"card": "4"})
    assert exc.value.rejected_type == "response.cast"

    services.cast_response(room, voter, item.id, {"card": "4"})

    stored = Response.objects.get(item=item, participant=voter)
    assert stored.payload == {"card": "4"}


@pytest.mark.django_db
def test_same_participant_answers_two_items_without_overwriting_either():
    room, fac, voter, rnd, item1 = _room()
    item2 = Item.objects.create(round=rnd, text="Budget", sequence=2)
    services.open_vote(room, fac)

    services.cast_response(room, voter, item1.id, {"card": "3"})
    services.cast_response(room, voter, item2.id, {"card": "5"})

    assert Response.objects.filter(round=rnd, participant=voter).count() == 2
    assert Response.objects.get(item=item1, participant=voter).payload == {"card": "3"}
    assert Response.objects.get(item=item2, participant=voter).payload == {"card": "5"}


@pytest.mark.django_db
def test_cast_response_refuses_off_deck_card_and_off_schema_payload():
    room, fac, voter, rnd, item = _room()
    services.open_vote(room, fac)

    # Carte hors du deck actif : le payload est structurellement valide (une
    # cle "card" de type str), mais la valeur n'est pas jouable.
    with pytest.raises(RoomError) as exc:
        services.cast_response(room, voter, item.id, {"card": "99"})
    assert exc.value.rejected_type == "response.cast"
    assert not Response.objects.filter(item=item, participant=voter).exists()

    # Cle en trop par rapport au payload_schema ({"card": str}).
    with pytest.raises(RoomError) as exc:
        services.cast_response(room, voter, item.id, {"card": "4", "extra": "nope"})
    assert exc.value.rejected_type == "response.cast"
    assert not Response.objects.filter(item=item, participant=voter).exists()

    # Cle manquante : "card" est requise par le schema.
    with pytest.raises(RoomError) as exc:
        services.cast_response(room, voter, item.id, {})
    assert exc.value.rejected_type == "response.cast"
    assert not Response.objects.filter(item=item, participant=voter).exists()

    # Type errone : le schema attend une str, pas un int.
    with pytest.raises(RoomError) as exc:
        services.cast_response(room, voter, item.id, {"card": 4})
    assert exc.value.rejected_type == "response.cast"
    assert not Response.objects.filter(item=item, participant=voter).exists()


@pytest.mark.django_db
def test_revealed_payload_carries_a_block_per_item():
    room, fac, voter, rnd, item1 = _room()
    item2 = Item.objects.create(round=rnd, text="Budget", sequence=2)
    other_voter = Participant.objects.create(
        room=room, token=generate_token(), display_name="Jordan", role=Role.VOTER
    )
    services.open_vote(room, fac)
    services.cast_response(room, voter, item1.id, {"card": "4"})
    services.cast_response(room, other_voter, item1.id, {"card": "6"})
    services.cast_response(room, voter, item2.id, {"card": "2"})
    services.reveal(room, fac)

    payload = services.revealed_payload(room)

    # Les cles plates historiques (tally/spread/votes au premier niveau) sont
    # parties avec l'alias `vote.cast` en fin de 5b (contrat §8.2.b) : seul
    # `itemResults` porte desormais le decompte.
    assert set(payload.keys()) == {"itemResults", "anonymous"}
    assert [block["itemId"] for block in payload["itemResults"]] == [item1.id, item2.id]
    assert payload["itemResults"][0]["tally"] == [
        {"cardValue": "4", "count": 1},
        {"cardValue": "6", "count": 1},
    ]
    assert payload["itemResults"][0]["spread"] == {"min": 4, "max": 6}
    assert payload["itemResults"][1]["tally"] == [{"cardValue": "2", "count": 1}]


@pytest.mark.django_db
def test_anonymous_round_hides_votes_on_every_item_block():
    """L'invariant d'anonymat doit tenir PAR ITEM : c'est le coeur de la tache 3.

    Depuis le retrait des cles plates historiques (fin 5b), `itemResults` est
    la SEULE forme du decompte : plus de mirroir a cote qui pourrait, lui,
    laisser fuiter `votes`.

    Verifie par mutation en developpant ce test : retirer la garde
    `if not anonymous` autour de `block["votes"] = ...` dans
    `realtime.services.revealed_payload` fait echouer ce test (des cles
    `votes` apparaissent dans CHAQUE bloc de `itemResults`), la remettre le
    fait repasser.
    """
    room, fac, voter, rnd, item1 = _room()
    item2 = Item.objects.create(round=rnd, text="Budget", sequence=2)
    rnd.is_anonymous = True
    rnd.save(update_fields=["is_anonymous"])
    services.open_vote(room, fac)
    services.cast_response(room, voter, item1.id, {"card": "4"})
    services.cast_response(room, voter, item2.id, {"card": "2"})
    services.reveal(room, fac)

    payload = services.revealed_payload(room)

    assert payload["anonymous"] is True
    assert len(payload["itemResults"]) == 2
    for block in payload["itemResults"]:
        assert "votes" not in block
    assert payload["itemResults"][0]["tally"] == [{"cardValue": "4", "count": 1}]
    assert payload["itemResults"][1]["tally"] == [{"cardValue": "2", "count": 1}]
    # Ceinture et bretelles : l'identifiant du votant n'apparait nulle part.
    assert str(voter.public_id) not in str(payload)


@pytest.mark.django_db
def test_build_state_sync_carries_my_responses():
    """`myVote` (la reponse du seul premier item) est parti avec les autres
    formes plates en fin de 5b — `myResponses` porte desormais toutes les
    reponses du participant, par item, seule forme du contrat."""
    room, fac, voter, rnd, item1 = _room()
    item2 = Item.objects.create(round=rnd, text="Budget", sequence=2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, item1.id, {"card": "4"})
    services.cast_response(room, voter, item2.id, {"card": "2"})

    state = services.build_state_sync(voter)

    assert "myVote" not in state
    assert state["myResponses"] == {
        str(item1.id): {"card": "4"},
        str(item2.id): {"card": "2"},
    }


@pytest.mark.django_db
def test_participation_requires_answering_every_item_of_the_round():
    """« A repondu » ne veut plus dire « a repondu a UN item » (tache 3, point 4
    de la relecture) : `_completed_participant_ids` compte les Response via
    `round=rnd` tandis que `n_items` vient de `rnd.items` — deux sources, donc
    exactement le genre d'ecart qu'un test doit figer plutot que verifier a la
    main."""
    room, fac, voter, rnd, item1 = _room()
    item2 = Item.objects.create(round=rnd, text="Budget", sequence=2)
    services.open_vote(room, fac)
    services.cast_response(room, voter, item1.id, {"card": "4"})

    # A mi-chemin : un seul des deux items a une reponse. Pas complet.
    midway = services.participation(room)
    assert midway == {"voted": 0, "total": 2, "votedIds": []}
    midway_list = {p["participantId"]: p["hasVoted"] for p in services.participants_list(room)}
    assert midway_list[str(voter.public_id)] is False

    services.cast_response(room, voter, item2.id, {"card": "2"})

    # Les deux items repondus : complet.
    done = services.participation(room)
    assert done == {"voted": 1, "total": 2, "votedIds": [str(voter.public_id)]}
    assert {p["participantId"]: p["hasVoted"] for p in services.participants_list(room)} == {
        str(fac.public_id): False,
        str(voter.public_id): True,
    }
