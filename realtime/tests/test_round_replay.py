"""Rejouer un round deja acte, et ce que l'agenda en dit (design 2026-09-11 §4).

L'ancien `select_subject` creait un round NEUF des que le precedent etait acte.
`select_round` doit garder cette semantique, sans quoi le re-vote herite du deck
fige, du mode d'anonymat et du `Result` du tour precedent.
"""
import pytest
from django.contrib.auth import get_user_model

from decks.models import Deck
from decks.seed import create_standard_deck
from realtime import services
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Result, Role, Room, RoundState
from rooms.snapshot import build_deck_snapshot

User = get_user_model()


def _second_deck(vote_type):
    """Un second deck jouable. Une seule carte suffit a distinguer les snapshots,
    et sa valeur n'existe PAS dans le deck standard ("1".."7") : c'est ce qui
    rend visible un snapshot perime."""
    deck = Deck.objects.create(vote_type=vote_type, is_standard=False, card_back_image="decks/backs/b.webp")
    deck.set_current_language("en")
    deck.name = "Fibonacci"
    deck.save()
    deck.cards.create(value="13", slug="thirteen", order=1, background_image="decks/cards/13.webp")
    return deck


@pytest.fixture
def room_with_two_decks(db):
    standard = create_standard_deck()
    other = _second_deck(standard.vote_type)
    snapshots = [build_deck_snapshot(standard), build_deck_snapshot(other)]
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=standard.vote_type,
        deck_snapshot=snapshots[0],
        deck_snapshots=snapshots,
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(
        room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR
    )
    voter = Participant.objects.create(
        room=room, token=generate_token(), display_name="Alex", role=Role.VOTER
    )
    return room, fac, voter, standard, other


def _play(room, fac, voter, card):
    services.open_vote(room, fac)
    services.cast_vote(room, voter, card)
    services.reveal(room, fac)
    return services.act_result(room, fac, card)


@pytest.mark.django_db
def test_replaying_an_acted_round_opens_a_new_one_with_a_copy_of_its_items(room_with_two_decks):
    """C2 : trois symptomes mesures d'un round acte rejoue EN PLACE — le deck
    perime rediffuse (premier vote rejete en « Unknown card value »), l'anonymat
    colle, et le `Result` du premier tour ECRASE par le second."""
    room, fac, voter, standard, other = room_with_two_decks
    services.set_current_item(room, fac, "Budget ?")
    first = services.current_round(room)
    first_id = first.id
    # Mode anonyme du premier tour, pose directement (l'option est reservee aux
    # equipes payantes, hors sujet ici) : il ne doit pas survivre au re-vote.
    first.is_anonymous = True
    first.save(update_fields=["is_anonymous"])
    _play(room, fac, voter, "4")

    # Le facilitateur change de deck entre les deux tours, ce qu'un round ACTED
    # autorise, puis reprend le sujet dans l'agenda.
    services.select_deck(room, fac, other.pk)
    out = services.select_round(room, fac, first_id)

    assert out["roundId"] != first_id
    assert out["text"] == "Budget ?"
    replay = services.current_round(room)
    assert replay.state == RoundState.IDLE
    # Le snapshot perime est parti : `open_vote` regelera le deck ACTIF. Et c'est
    # bien le deck ACTIF qui repart vers les clients — le rejouer en place leur
    # rediffusait la main du tour precedent, dont le premier vote revenait ensuite
    # en « Unknown card value ».
    assert replay.deck_snapshot is None
    assert services.active_deck_snapshot(room)["deckId"] == other.pk
    assert replay.is_anonymous is False
    # L'item est une COPIE tracee, pas l'item du tour precedent : le reformuler ne
    # reecrit pas ce qui a ete decide (design §4).
    old_item = Item.objects.get(round_id=first_id)
    new_item = replay.items.get()
    assert new_item.id != old_item.id
    assert new_item.origin_item_id == old_item.id

    # Le premier vote du re-vote joue sur le deck actif, et non sur celui du tour
    # passe : c'est le « Unknown card value » de la relecture.
    _play(room, fac, voter, "13")

    assert Item.objects.get(round_id=first_id).text == "Budget ?"
    # Deux resultats distincts, pas un ecrase.
    assert sorted(
        Result.objects.filter(round__room=room).values_list("chosen_value", flat=True)
    ) == ["13", "4"]
    assert Result.objects.get(round_id=first_id).chosen_value == "4"


@pytest.mark.django_db
def test_agenda_forgets_the_result_of_a_round_sent_back_to_idle(room_with_two_decks):
    """I1 : `vote.reset` remet le round a IDLE en LAISSANT son `Result`. Sans le
    filtre d'etat, un round reinitialise reapparait « done » avec son ancienne
    valeur alors qu'il est a rejouer. Le test verifie les deux sens : un round
    acte reste « done », un round acte PUIS reinitialise redevient « pending »."""
    room, fac, voter, _, _ = room_with_two_decks
    services.set_current_item(room, fac, "Budget ?")
    a_id = services.current_round(room).id
    _play(room, fac, voter, "4")

    b_id = services.add_scenario_item(room, fac, "Embauche ?")
    services.select_round(room, fac, b_id)

    agenda = {e["id"]: e for e in services.build_agenda(room)}
    assert agenda[a_id]["status"] == "done"
    assert agenda[a_id]["result"] == "4"

    # Deuxieme tour, acte puis renvoye a idle par le facilitateur.
    _play(room, fac, voter, "5")
    services.reset_round(room, fac)
    c_id = services.add_scenario_item(room, fac, "Conges ?")
    services.select_round(room, fac, c_id)

    agenda = {e["id"]: e for e in services.build_agenda(room)}
    assert agenda[b_id]["status"] == "pending"
    assert agenda[b_id]["result"] is None
    # Le round reellement acte, lui, n'a pas bouge.
    assert agenda[a_id]["status"] == "done"
    assert agenda[a_id]["result"] == "4"
    assert agenda[c_id]["status"] == "current"
