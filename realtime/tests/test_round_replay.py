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
from realtime.services import RoomError
from realtime.tests.helpers import cast_first_item
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
    cast_first_item(room, voter, card)
    services.reveal(room, fac)
    return services.act_result(room, fac, card)


@pytest.mark.django_db
def test_replaying_an_acted_round_opens_a_new_one_with_a_copy_of_its_items(room_with_two_decks):
    """C2, revu au round de correction 2 (task 1, design 5c). Rejouer une
    activite ACTEE ouvre un round neuf qui copie ses items, MAIS reste la MEME
    activite : il garde le deck (donc le type) avec lequel elle a ete jouee, pas
    celui devenu actif sur la room entretemps (design decide 2026-09-12 :
    rejouer = meme activite, reponses neuves). Seuls l'anonymat et le `Result`
    ne survivent pas au rejeu — pas le deck.

    Avant ce round de correction, `replay.deck_snapshot` etait laisse a `None`
    et `open_vote` le regelait sur le deck ACTIF de la room : un round de Dot
    Voting rejoue apres que le facilitateur soit passe au Poker serait alors
    devenu... du Poker. Ce test verifiait ce (faux) comportement ; il verifie
    desormais l'inverse.
    """
    room, fac, voter, standard, other = room_with_two_decks
    services.set_current_item(room, fac, "Budget ?")
    first = services.current_round(room)
    first_id = first.id
    # Mode anonyme du premier tour, pose directement (l'option est reservee aux
    # equipes payantes, hors sujet ici) : il ne doit pas survivre au re-vote.
    first.is_anonymous = True
    first.save(update_fields=["is_anonymous"])
    _play(room, fac, voter, "4")

    # Le facilitateur change le deck ACTIF de la room entre les deux tours —
    # mais le round rejoue doit garder LE SIEN (celui fige quand il a ete joue
    # la premiere fois), pas courir apres le nouveau deck actif.
    services.select_deck(room, fac, other.pk)
    out = services.select_round(room, fac, first_id)

    assert out["roundId"] != first_id
    assert out["text"] == "Budget ?"
    replay = services.current_round(room)
    assert replay.state == RoundState.IDLE
    # Le round rejoue a copie le deck du round SOURCE (standard), pas celui,
    # devenu actif entretemps, de la room (other) : rejouer, c'est la MEME
    # activite. `room.deck_snapshot` (le champ brut) est bien passe a other,
    # mais `active_deck_snapshot` (ce qui part vers les clients) privilegie le
    # snapshot propre du round, comme partout ailleurs dans ce module.
    assert replay.deck_snapshot["deckId"] == standard.pk
    assert room.deck_snapshot["deckId"] == other.pk
    assert services.active_deck_snapshot(room)["deckId"] == standard.pk
    assert replay.is_anonymous is False
    # L'item est une COPIE tracee, pas l'item du tour precedent : le reformuler ne
    # reecrit pas ce qui a ete decide (design §4).
    old_item = Item.objects.get(round_id=first_id)
    new_item = replay.items.get()
    assert new_item.id != old_item.id
    assert new_item.origin_item_id == old_item.id

    services.open_vote(room, fac)
    # Une valeur qui n'existe QUE dans le deck devenu actif de la room (other)
    # est refusee sur le rejeu : le round a garde SON deck (standard), pas
    # celui de la room.
    with pytest.raises(RoomError):
        cast_first_item(room, voter, "13")
    cast_first_item(room, voter, "5")
    services.reveal(room, fac)
    services.act_result(room, fac, "5")

    assert Item.objects.get(round_id=first_id).text == "Budget ?"
    # Deux resultats distincts, pas un ecrase.
    assert sorted(
        Result.objects.filter(round__room=room).values_list("chosen_value", flat=True)
    ) == ["4", "5"]
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
