"""La reponse est la contribution d'un participant a un ITEM (design §3).

`payload` est un JSON valide par le schema que declare le type d'activite : une
seule table pour toutes les activites, jamais une table par activite.
"""
import pytest

from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Response, Room, Round, RoundState
from rooms.snapshot import build_deck_snapshot


def _round_with_item(deck):
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=deck.vote_type,
        deck_snapshot=build_deck_snapshot(deck),
    )
    room.touch(save=False)
    room.save()
    rnd = Round.objects.create(room=room, state=RoundState.OPEN)
    item = Item.objects.create(round=rnd, text="Budget ?", sequence=1)
    return room, rnd, item


@pytest.mark.django_db
def test_response_carries_a_json_payload(standard_deck):
    room, rnd, item = _round_with_item(standard_deck)
    p = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")

    r = Response.objects.create(round=rnd, participant=p, item=item, payload={"card": "4"})
    r.refresh_from_db()

    assert r.payload == {"card": "4"}
    assert r.item_id == item.id


@pytest.mark.django_db
def test_responses_die_with_their_item(standard_deck):
    """Retirer un item emporte les reponses qui le visaient : elles n'ont plus
    d'objet, et les laisser orphelines fausserait tout decompte ulterieur."""
    room, rnd, item = _round_with_item(standard_deck)
    p = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")
    Response.objects.create(round=rnd, participant=p, item=item, payload={"card": "4"})

    item.delete()

    assert Response.objects.count() == 0


@pytest.mark.django_db
def test_payload_defaults_to_an_empty_dict(standard_deck):
    room, rnd, item = _round_with_item(standard_deck)
    p = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")

    r = Response.objects.create(round=rnd, participant=p, item=item)

    assert r.payload == {}
