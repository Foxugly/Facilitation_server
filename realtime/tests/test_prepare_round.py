"""Two-step round flow: prepare_round (step 1) composes + announces the round in one
atomic call, open_vote (step 2) opens it.

The key regression this guards: composing a round from scratch — including the reveal
mode — must not error when no round exists yet (that ordering was the old
"toggle nominative -> popup" bug). prepare_round creates the idle round first, then
applies every detail against it.
"""
import pytest
from django.contrib.auth import get_user_model

from decks.models import Deck
from decks.seed import create_standard_deck
from realtime import services
from realtime.services import RoomError
from realtime.tests.helpers import cast_first_item
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, RoundState
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _fresh_room(team=None):
    """A room with a facilitator and a voter but NO subject/round yet — the state a
    brand-new room is in when the facilitator first opens the panel."""
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), team=team)
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return room, fac, voter


def _second_deck(vote_type):
    """Un second deck jouable, une seule carte suffit a distinguer les
    snapshots (meme helper que `test_round_type.py`)."""
    deck = Deck.objects.create(vote_type=vote_type, is_standard=False, card_back_image="decks/backs/b.webp")
    deck.set_current_language("en")
    deck.name = "Fibonacci"
    deck.save()
    deck.cards.create(value="13", slug="thirteen", order=1, background_image="decks/cards/13.webp")
    return deck


def _fresh_room_with_two_decks(team=None):
    """Comme `_fresh_room`, mais la room peut basculer vers un second deck —
    necessaire pour observer une ecriture de deck partiellement appliquee."""
    standard = create_standard_deck()
    other = _second_deck(standard.vote_type)
    snapshots = [build_deck_snapshot(standard), build_deck_snapshot(other)]
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(
        code=code, vote_type=standard.vote_type, deck_snapshot=snapshots[0],
        deck_snapshots=snapshots, team=team,
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return room, fac, voter, standard, other


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
    rnd = services._current_round(room)
    assert rnd is not None
    assert rnd.state == RoundState.IDLE  # prepared, NOT open


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
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)

    assert services.revealed_payload(room)["anonymous"] is False


@pytest.mark.django_db
def test_prepare_anonymous_is_applied_to_the_round(paid_team):
    room, fac, voter = _fresh_room(team=paid_team)
    services.prepare_round(room, fac, subject_text="Deploys", anonymous=True)
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)

    payload = services.revealed_payload(room)
    assert payload["anonymous"] is True
    assert all("votes" not in block for block in payload["itemResults"])


@pytest.mark.django_db
def test_prepare_anonymous_on_free_room_is_refused(db):
    room, fac, _ = _fresh_room()  # no team = free room
    with pytest.raises(RoomError) as exc:
        services.prepare_round(room, fac, subject_text="X", anonymous=True)
    assert exc.value.code == "forbidden.subscription_required"


@pytest.mark.django_db
def test_prepare_by_subject_id_selects_a_queued_subject(db):
    """`subject_id` designe desormais un ROUND (design 2026-09-11 §5) : queuer un
    second sujet, c'est ouvrir un second round via `add_scenario_item`."""
    room, fac, _ = _fresh_room()
    services.prepare_round(room, fac, subject_text="First")
    second_round_id = services.add_scenario_item(room, fac, "Second")

    summary = services.prepare_round(room, fac, subject_id=second_round_id)

    assert summary["subject"] == "Second"
    assert services._current_round(room).id == second_round_id


@pytest.mark.django_db
def test_prepare_round_rejects_atomically_leaving_deck_unchanged(db):
    """Un appel qui change de deck ET porte un reglage refuse (l'anonymat sur
    une room gratuite) ne doit RIEN laisser ecrit, deck compris : `deck_id`
    est applique avant `anonymous` dans le corps de `prepare_round`, donc sans
    transaction le deck de la room aurait deja bascule vers `other` quand
    `set_reveal_mode` leve.

    Verifie par mutation : retirer `@transaction.atomic` sur `prepare_round`
    fait echouer ce test (le deck de la room passe a `other` malgre le
    refus, et le round cree par `subject_text` survit). Voir le rapport de
    tache pour la trace."""
    room, fac, voter, standard, other = _fresh_room_with_two_decks()  # pas de team = room gratuite

    with pytest.raises(RoomError) as exc:
        services.prepare_round(room, fac, subject_text="X", deck_id=other.pk, anonymous=True)
    assert exc.value.code == "forbidden.subscription_required"

    room.refresh_from_db(fields=["deck_snapshot"])
    assert room.deck_snapshot["deckId"] == standard.pk
    # "Aucune ecriture" veut dire aucune : le round cree par `subject_text`
    # avant le rejet ne doit pas non plus survivre.
    assert not room.rounds.exists()


@pytest.mark.django_db
def test_voter_cannot_prepare(db):
    room, _, voter = _fresh_room()
    with pytest.raises(RoomError) as exc:
        services.prepare_round(room, voter, subject_text="X")
    assert exc.value.code == "forbidden.not_facilitator"
