"""Reveal mode: nominative by default, anonymous as a paid-team option.

The invariant that matters: when a round is anonymous the server must not emit any
participant -> card link at all. Hiding it client-side would be façade — a WS frame
is readable in any browser's devtools.
"""
import pytest
from django.contrib.auth import get_user_model

from decks.seed import create_standard_deck
from realtime import services
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, RoundState, Subject, VoteSession
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _room(team=None):
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), team=team)
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    subject = Subject.objects.create(room=room, text="Deploys")
    session = VoteSession.objects.create(room=room, subject=subject, facilitator=fac)
    room.current_session = session
    room.save(update_fields=["current_session"])
    return room, fac, voter, session


@pytest.fixture
def paid_team(db):
    owner = User.objects.create_user(email="o@example.com", password="pw12345678", display_name="O")
    team = Team.objects.create(name="Acme", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    return team


@pytest.mark.django_db
def test_default_is_nominative_and_votes_are_emitted(db):
    room, fac, voter, _ = _room()
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")
    services.reveal(room, fac)

    payload = services.revealed_payload(room)

    assert payload["anonymous"] is False
    assert [v["cardValue"] for v in payload["votes"]] == ["4"]
    assert payload["votes"][0]["participantId"] == str(voter.public_id)


@pytest.mark.django_db
def test_anonymous_round_emits_no_participant_card_link(paid_team):
    room, fac, voter, _ = _room(team=paid_team)
    services.set_reveal_mode(room, fac, True)
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")
    services.reveal(room, fac)

    payload = services.revealed_payload(room)

    assert payload["anonymous"] is True
    assert "votes" not in payload
    assert payload["tally"] == [{"cardValue": "4", "count": 1}]
    # Belt and braces: the participant's id must appear nowhere in the payload.
    assert str(voter.public_id) not in str(payload)


@pytest.mark.django_db
def test_free_room_cannot_anonymise(db):
    room, fac, _, _ = _room()  # no team = free/anonymous room

    with pytest.raises(RoomError) as exc:
        services.set_reveal_mode(room, fac, True)
    assert exc.value.code == "forbidden.subscription_required"


@pytest.mark.django_db
def test_voter_cannot_set_the_mode(paid_team):
    room, _, voter, _ = _room(team=paid_team)

    with pytest.raises(RoomError) as exc:
        services.set_reveal_mode(room, voter, True)
    assert exc.value.code == "forbidden.not_facilitator"


@pytest.mark.django_db
def test_mode_cannot_flip_once_voting_is_open(paid_team):
    """Voters were told the mode before playing their card."""
    room, fac, _, _ = _room(team=paid_team)
    services.open_vote(room, fac)

    with pytest.raises(RoomError) as exc:
        services.set_reveal_mode(room, fac, True)
    assert exc.value.code == "state.invalid_transition"


@pytest.mark.django_db
def test_state_sync_announces_the_mode_to_everyone(paid_team):
    room, fac, voter, _ = _room(team=paid_team)
    services.set_reveal_mode(room, fac, True)

    for who in (fac, voter):
        payload = services.build_state_sync(who)
        assert payload["reveal"]["anonymous"] is True
        assert payload["reveal"]["canAnonymise"] is True


@pytest.mark.django_db
def test_free_room_state_sync_says_it_cannot_anonymise(db):
    room, fac, _, _ = _room()

    assert services.build_state_sync(fac)["reveal"] == {"anonymous": False, "canAnonymise": False}
