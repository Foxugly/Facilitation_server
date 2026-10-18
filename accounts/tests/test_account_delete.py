"""GDPR: delete one's own account (audit 2026-10-18, I6).

Same contract as trainingmanager_server: POST /api/v1/auth/account/delete/ with
{current_password} -> 204; 400 current_password_invalid; 409 owns_teams.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from accounts.models import MagicLinkToken
from decks.seed import create_standard_deck
from rooms.codes import generate_token
from rooms.models import Participant, Role, Room
from rooms.snapshot import build_deck_snapshot
from teams.models import Invitation, Team, TeamMembership, TeamRole

User = get_user_model()
PASSWORD = "sup3rSecret!"
URL = "/api/v1/auth/account/delete/"


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="leaver@example.com", password=PASSWORD, display_name="Leaver", email_confirmed=True
    )


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_anonymous_is_refused(db):
    assert APIClient().post(URL, {"current_password": PASSWORD}, format="json").status_code == 401


def test_wrong_password_keeps_the_account(client, user):
    resp = client.post(URL, {"current_password": "nope"}, format="json")
    assert resp.status_code == 400
    assert resp.json()["code"] == "current_password_invalid"
    assert User.objects.filter(pk=user.pk).exists()


def test_missing_password_is_a_field_error(client):
    resp = client.post(URL, {}, format="json")
    assert resp.status_code == 400
    assert "current_password" in resp.json()


def test_team_owner_must_hand_over_first(client, user):
    team = Team.objects.create(name="Mine", owner=user)
    TeamMembership.objects.create(team=team, user=user, role=TeamRole.OWNER)
    resp = client.post(URL, {"current_password": PASSWORD}, format="json")
    assert resp.status_code == 409
    assert resp.json()["code"] == "owns_teams"
    assert Team.objects.filter(pk=team.pk).exists()


def test_delete_erases_the_account_and_its_traces(client, user):
    owner = User.objects.create_user(email="boss@example.com", password=PASSWORD)
    team = Team.objects.create(name="Theirs", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    TeamMembership.objects.create(team=team, user=user, role=TeamRole.MEMBER)
    deck = create_standard_deck()
    room = Room(code="ABCDEF", vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), team=team)
    room.touch(save=False)
    room.save()
    seat = Participant.objects.create(
        room=room, token=generate_token(), display_name="leaver@example.com", role=Role.VOTER, user=user
    )
    Invitation.objects.create(team=team, email="Leaver@example.com", expires_at=room.expires_at)
    MagicLinkToken.objects.create(user=user, expires_at=room.expires_at)

    resp = client.post(URL, {"current_password": PASSWORD}, format="json")

    assert resp.status_code == 204
    assert not User.objects.filter(pk=user.pk).exists()
    assert not TeamMembership.objects.filter(user_id=user.pk).exists()
    assert not MagicLinkToken.objects.filter(user_id=user.pk).exists()
    assert not Invitation.objects.filter(email__iexact="leaver@example.com").exists()
    seat.refresh_from_db()
    assert seat.user_id is None
    assert "leaver" not in seat.display_name.lower()
    assert Team.objects.filter(pk=team.pk).exists()


def test_refresh_tokens_die_with_the_account(user):
    c = APIClient()
    login = c.post("/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json").json()
    c.credentials(HTTP_AUTHORIZATION="Bearer " + login["access"])
    assert c.post(URL, {"current_password": PASSWORD}, format="json").status_code == 204
    c.credentials()
    resp = c.post("/api/v1/auth/token/refresh/", {"refresh": login["refresh"]}, format="json")
    assert resp.status_code == 401
