import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from rooms.models import Participant, Room
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="m@example.com", password="pw12345678", display_name="Mia")


@pytest.fixture
def team(member):
    t = Team.objects.create(name="Squad", owner=member)
    TeamMembership.objects.create(team=t, user=member, role=TeamRole.OWNER)
    return t


@pytest.mark.django_db
def test_create_team_room_requires_membership(standard_deck, team, member):
    anon = APIClient()
    assert anon.post("/api/v1/rooms", {"team": team.id}, format="json").status_code == 401

    other = User.objects.create_user(email="o@example.com", password="pw12345678")
    oc = APIClient()
    oc.force_authenticate(other)
    assert oc.post("/api/v1/rooms", {"team": team.id}, format="json").status_code == 403

    mc = APIClient()
    mc.force_authenticate(member)
    resp = mc.post("/api/v1/rooms", {"team": team.id, "title": "Sprint"}, format="json")
    assert resp.status_code == 201 and resp.json()["isTeam"] is True
    room = Room.objects.get(code=resp.json()["code"])
    assert room.team_id == team.id
    p = Participant.objects.get(token=resp.json()["participantToken"])
    assert p.user_id == member.id and p.display_name == "Mia"


@pytest.mark.django_db
def test_team_room_snapshot_uses_team_appearance(standard_deck, team, member):
    team.card_back_color = "#101010"
    team.felt_color = "#00aa66"
    team.save(update_fields=["card_back_color", "felt_color"])
    mc = APIClient()
    mc.force_authenticate(member)
    snap = mc.post("/api/v1/rooms", {"team": team.id}, format="json").json()["deckSnapshot"]
    assert snap["theme"] == {"cardBackColor": "#101010", "feltColor": "#00aa66"}


@pytest.mark.django_db
def test_team_room_join_members_only_and_rejoin(standard_deck, team, member):
    mc = APIClient()
    mc.force_authenticate(member)
    code = mc.post("/api/v1/rooms", {"team": team.id}, format="json").json()["code"]

    bob = User.objects.create_user(email="bob@example.com", password="pw12345678", display_name="Bob")
    TeamMembership.objects.create(team=team, user=bob, role=TeamRole.MEMBER)
    bc = APIClient()
    bc.force_authenticate(bob)
    token1 = bc.post(f"/api/v1/rooms/{code}/join", {}, format="json").json()["participantToken"]
    # re-join reuses the same participant (no duplicate seat)
    token2 = bc.post(f"/api/v1/rooms/{code}/join", {}, format="json").json()["participantToken"]
    assert token1 == token2
    assert Participant.objects.filter(room__code=code, user=bob).count() == 1

    stranger = User.objects.create_user(email="s@example.com", password="pw12345678")
    sc = APIClient()
    sc.force_authenticate(stranger)
    assert sc.post(f"/api/v1/rooms/{code}/join", {}, format="json").status_code == 403
    assert APIClient().post(f"/api/v1/rooms/{code}/join", {}, format="json").status_code == 401


@pytest.mark.django_db
def test_team_room_is_not_ephemeral(standard_deck, team, member):
    mc = APIClient()
    mc.force_authenticate(member)
    code = mc.post("/api/v1/rooms", {"team": team.id}, format="json").json()["code"]
    room = Room.objects.get(code=code)
    room.expires_at = timezone.now() - timezone.timedelta(hours=1)  # past
    room.save(update_fields=["expires_at"])
    assert room.is_live is True  # the 8h window of free rooms does not apply


@pytest.mark.django_db
def test_anonymous_room_still_works(standard_deck):
    resp = APIClient().post("/api/v1/rooms", {"username": "Sam"}, format="json")
    assert resp.status_code == 201 and resp.json()["isTeam"] is False


@pytest.mark.django_db
def test_member_without_display_name_never_appears_under_email(standard_deck, team, member):
    """Product decision 2026-10-21: the participant name is broadcast to the whole
    room (state.sync, participant.joined), so it must not fall back to the e-mail
    nor its local part."""
    nameless = User.objects.create_user(email="zoe.secret@example.com", password="pw12345678")
    TeamMembership.objects.create(team=team, user=nameless, role=TeamRole.MEMBER)
    zc = APIClient()
    zc.force_authenticate(nameless)

    # As facilitator (room creation): first seat.
    created = zc.post("/api/v1/rooms", {"team": team.id}, format="json").json()
    fac = Participant.objects.get(token=created["participantToken"])
    assert fac.display_name == "Participant 1"

    # As voter (join): numbered by seat.
    mc = APIClient()
    mc.force_authenticate(member)
    code = mc.post("/api/v1/rooms", {"team": team.id}, format="json").json()["code"]
    token = zc.post(f"/api/v1/rooms/{code}/join", {}, format="json").json()["participantToken"]
    seat = Participant.objects.get(token=token)
    assert seat.display_name == "Participant 2"
    for p in Participant.objects.filter(user=nameless):
        assert "zoe" not in p.display_name.lower() and "@" not in p.display_name


@pytest.mark.django_db
def test_migration_renames_seats_named_after_email(standard_deck, team, member):
    import importlib

    from django.apps import apps

    from rooms.codes import generate_token

    mig = importlib.import_module("rooms.migrations.0023_participant_name_not_email")
    nameless = User.objects.create_user(email="Leak@Example.com", password="pw12345678")
    TeamMembership.objects.create(team=team, user=nameless, role=TeamRole.MEMBER)
    mc = APIClient()
    mc.force_authenticate(member)
    room = Room.objects.get(code=mc.post("/api/v1/rooms", {"team": team.id}, format="json").json()["code"])
    leaked = Participant.objects.create(room=room, token=generate_token(), display_name="leak@example.com", user=nameless)
    anon = Participant.objects.create(room=room, token=generate_token(), display_name="a@b.c")  # chosen by a guest

    mig.forwards(apps, None)

    leaked.refresh_from_db()
    anon.refresh_from_db()
    assert leaked.display_name == "Participant 2"
    assert anon.display_name == "a@b.c"
    assert Participant.objects.get(room=room, user=member).display_name == "Mia"
