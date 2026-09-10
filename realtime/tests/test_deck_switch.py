"""Live deck switching in a room (multi-deck rooms).

A team enables several poker types; the room freezes them all and the facilitator
switches between rounds. The guards that matter: only the facilitator switches,
only to a deck the room actually froze, and never under an open round.
"""
import pytest
from django.contrib.auth import get_user_model

from decks.models import Deck
from decks.seed import create_standard_deck
from realtime import services
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, RoundState, Subject, VoteSession
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _second_deck(vote_type):
    """A second playable deck (one card is enough to tell the snapshots apart)."""
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
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(
        code=code, vote_type=standard.vote_type, deck_snapshot=snapshots[0],
        deck_snapshots=snapshots, title="Retro",
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return room, fac, voter, standard, other


@pytest.mark.django_db
def test_facilitator_switches_the_active_deck(room_with_two_decks):
    room, fac, _, standard, other = room_with_two_decks

    snapshot = services.select_deck(room, fac, other.pk)

    assert snapshot["deckId"] == other.pk
    room.refresh_from_db()
    assert room.deck_snapshot["deckId"] == other.pk
    # The frozen catalogue is untouched by a switch.
    assert {s["deckId"] for s in room.deck_snapshots} == {standard.pk, other.pk}


@pytest.mark.django_db
def test_voter_cannot_switch(room_with_two_decks):
    room, _, voter, _, other = room_with_two_decks

    with pytest.raises(RoomError) as exc:
        services.select_deck(room, voter, other.pk)
    assert exc.value.code == "forbidden.not_facilitator"


@pytest.mark.django_db
def test_cannot_switch_to_a_deck_the_room_did_not_freeze(room_with_two_decks):
    room, fac, _, standard, _ = room_with_two_decks
    outsider = _second_deck(standard.vote_type)

    with pytest.raises(RoomError) as exc:
        services.select_deck(room, fac, outsider.pk)
    assert exc.value.code == "state.invalid_transition"


@pytest.mark.django_db
def test_cannot_switch_mid_round(room_with_two_decks):
    """Votes already cast reference the current deck's values."""
    room, fac, _, _, other = room_with_two_decks
    subject = Subject.objects.create(room=room, text="Deploys")
    session = VoteSession.objects.create(room=room, subject=subject, facilitator=fac, state=RoundState.OPEN)
    room.current_session = session
    room.save(update_fields=["current_session"])

    with pytest.raises(RoomError) as exc:
        services.select_deck(room, fac, other.pk)
    assert exc.value.code == "state.invalid_transition"


@pytest.mark.django_db
def test_open_round_freezes_its_deck_and_survives_a_later_switch(room_with_two_decks):
    """A past round keeps its own deck, so history can't be relabelled by a switch."""
    room, fac, _, standard, other = room_with_two_decks
    subject = Subject.objects.create(room=room, text="Deploys")
    session = VoteSession.objects.create(room=room, subject=subject, facilitator=fac)
    room.current_session = session
    room.save(update_fields=["current_session"])

    services.open_vote(room, fac)
    session.refresh_from_db()
    assert session.deck_snapshot["deckId"] == standard.pk

    # Close the round, then switch: the played round keeps the deck it used.
    session.state = RoundState.ACTED
    session.save(update_fields=["state"])
    services.select_deck(room, fac, other.pk)

    session.refresh_from_db()
    assert session.deck_snapshot["deckId"] == standard.pk
    room.refresh_from_db()
    assert room.deck_snapshot["deckId"] == other.pk


@pytest.mark.django_db
def test_state_sync_exposes_the_catalogue_and_the_deck_in_play(room_with_two_decks):
    room, fac, _, standard, other = room_with_two_decks

    payload = services.build_state_sync(fac)

    assert payload["deckSnapshot"]["deckId"] == standard.pk
    assert {d["deckId"] for d in payload["availableDecks"]} == {standard.pk, other.pk}


@pytest.mark.django_db
def test_vote_values_follow_the_round_deck_not_the_room(room_with_two_decks):
    """A card valid in the room's new deck must not be accepted in a round frozen
    on the old one."""
    room, fac, voter, _, other = room_with_two_decks
    subject = Subject.objects.create(room=room, text="Deploys")
    session = VoteSession.objects.create(room=room, subject=subject, facilitator=fac)
    room.current_session = session
    room.save(update_fields=["current_session"])
    services.open_vote(room, fac)  # freezes the standard deck (values "1".."7")

    with pytest.raises(RoomError):
        services.cast_vote(room, voter, "13")  # only exists in the other deck
