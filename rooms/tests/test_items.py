"""L'item est l'unite qu'une activite manipule (design 2026-09-11 §3).

Porte par le ROUND et non par la room : un round est une activite jouee sur N
items, et un item copie d'un round a l'autre ne doit pas se reecrire a la source.
"""
import pytest

from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Round, RoundState, Room
from rooms.snapshot import build_deck_snapshot


def _room(deck):
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=deck.vote_type,
        deck_snapshot=build_deck_snapshot(deck),
    )
    room.touch(save=False)
    room.save()
    return room


@pytest.mark.django_db
def test_items_are_ordered_by_sequence(standard_deck):
    room = _room(standard_deck)
    rnd = Round.objects.create(room=room, state=RoundState.IDLE)
    Item.objects.create(round=rnd, text="B", sequence=2)
    Item.objects.create(round=rnd, text="A", sequence=1)

    assert [i.text for i in rnd.items.all()] == ["A", "B"]


@pytest.mark.django_db
def test_items_die_with_their_round(standard_deck):
    room = _room(standard_deck)
    rnd = Round.objects.create(room=room, state=RoundState.IDLE)
    Item.objects.create(round=rnd, text="A", sequence=1)

    rnd.delete()

    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_author_survives_the_participant_leaving(standard_deck):
    """Un post-it ne disparait pas parce que son auteur a quitte la salle : le
    lien se vide, l'idee reste (design §3)."""
    room = _room(standard_deck)
    rnd = Round.objects.create(room=room, state=RoundState.IDLE)
    author = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")
    item = Item.objects.create(round=rnd, text="A", sequence=1, author=author)

    author.delete()
    item.refresh_from_db()

    assert item.text == "A"
    assert item.author_id is None
