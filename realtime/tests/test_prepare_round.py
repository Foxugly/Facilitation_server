"""Two-step round flow: prepare_round (step 1) composes + announces the round in one
atomic call, open_vote (step 2) opens it.

The key regression this guards: composing a round from scratch — including the reveal
mode — must not error when no session exists yet (that ordering was the old
"toggle nominative -> popup" bug). prepare_round creates the idle session first, then
applies every detail against it.
"""
import pytest
from django.contrib.auth import get_user_model

from decks.seed import create_standard_deck
from realtime import services
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, RoundState, Subject
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _fresh_room(team=None):
    """A room with a facilitator and a voter but NO subject/session yet — the state a
    brand-new room is in when the facilitator first opens the panel."""
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), team=team)
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return room, fac, voter


@pytest.fixture
def paid_team(db):
    owner = User.objects.create_user(email="o@example.com", password="pw12345678", display_name="O")
    team = Team.objects.create(name="Acme", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    return team


@pytest.mark.django_db
def test_prepare_from_scratch_creates_idle_round(db):
    room, fac, _ = _fresh_room()
    summary = services.prepare_round(
        room, fac, subject_text="Deploys", timer_enabled=True, timer_seconds=30
    )

    assert summary["subject"] == "Deploys"
    # The timer is a TEAM feature: on an anonymous room the fields are silently
    # ignored (a stale client must still be able to prepare its round).
    assert summary["timerEnabled"] is False
    assert summary["anonymous"] is False
    session = services._current_session(room)
    assert session is not None
    assert session.state == RoundState.IDLE  # prepared, NOT open


@pytest.mark.django_db
def test_prepare_applies_the_timer_on_a_team_room(paid_team):
    room, fac, _ = _fresh_room(team=paid_team)
    summary = services.prepare_round(
        room, fac, subject_text="Deploys", timer_enabled=True, timer_seconds=30
    )
    assert summary["timerEnabled"] is True
    assert summary["timerSeconds"] == 30


@pytest.mark.django_db
def test_prepare_nominative_on_fresh_room_does_not_error(db):
    """The old bug: toggling the reveal mode before any subject existed popped an
    error. Going through prepare_round it must be a no-op-safe default."""
    room, fac, _ = _fresh_room()
    summary = services.prepare_round(room, fac, subject_text="X", anonymous=False)
    assert summary["anonymous"] is False


@pytest.mark.django_db
def test_prepared_round_then_opens(db):
    room, fac, voter = _fresh_room()
    services.prepare_round(room, fac, subject_text="Deploys")
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")
    services.reveal(room, fac)

    assert services.revealed_payload(room)["anonymous"] is False


@pytest.mark.django_db
def test_prepare_anonymous_is_applied_to_the_round(paid_team):
    room, fac, voter = _fresh_room(team=paid_team)
    services.prepare_round(room, fac, subject_text="Deploys", anonymous=True)
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")
    services.reveal(room, fac)

    payload = services.revealed_payload(room)
    assert payload["anonymous"] is True
    assert "votes" not in payload


@pytest.mark.django_db
def test_prepare_anonymous_on_free_room_is_refused(db):
    room, fac, _ = _fresh_room()  # no team = free room
    with pytest.raises(RoomError) as exc:
        services.prepare_round(room, fac, subject_text="X", anonymous=True)
    assert exc.value.code == "forbidden.subscription_required"


@pytest.mark.django_db
def test_prepare_by_subject_id_selects_a_queued_subject(db):
    room, fac, _ = _fresh_room()
    services.prepare_round(room, fac, subject_text="First")
    second = Subject.objects.create(room=room, text="Second", sequence=2)

    summary = services.prepare_round(room, fac, subject_id=second.id)

    assert summary["subject"] == "Second"
    assert services._current_session(room).subject_id == second.id


@pytest.mark.django_db
def test_voter_cannot_prepare(db):
    room, _, voter = _fresh_room()
    with pytest.raises(RoomError) as exc:
        services.prepare_round(room, voter, subject_text="X")
    assert exc.value.code == "forbidden.not_facilitator"
