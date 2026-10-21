"""Product decision 2026-10-21: teammates see each other's display name, never the
e-mail. The e-mail goes only to the team's managers (owner + manager) and to the
user themself."""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.fixture
def squad(db):
    owner = User.objects.create_user(email="owner@example.com", password="pw12345678", display_name="Olga")
    manager = User.objects.create_user(email="manager@example.com", password="pw12345678", display_name="Max")
    alice = User.objects.create_user(email="alice@example.com", password="pw12345678", display_name="Alice")
    bob = User.objects.create_user(email="bob@example.com", password="pw12345678")  # no display name
    team = Team.objects.create(name="Squad", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    TeamMembership.objects.create(team=team, user=manager, role=TeamRole.MANAGER)
    TeamMembership.objects.create(team=team, user=alice, role=TeamRole.MEMBER)
    TeamMembership.objects.create(team=team, user=bob, role=TeamRole.MEMBER)
    return team, {"owner": owner, "manager": manager, "alice": alice, "bob": bob}


def _as(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _emails_seen(client, team):
    resp = client.get(f"/api/v1/teams/{team.id}/members/")
    assert resp.status_code == 200
    return {m["user"]["id"]: m["user"]["email"] for m in resp.json()}


@pytest.mark.django_db
def test_member_sees_only_own_email(squad):
    team, u = squad
    seen = _emails_seen(_as(u["alice"]), team)
    assert seen[u["alice"].id] == "alice@example.com"
    for other in ("owner", "manager", "bob"):
        assert seen[u[other].id] is None
    body = _as(u["alice"]).get(f"/api/v1/teams/{team.id}/members/").content.decode()
    for addr in ("owner@", "manager@", "bob@"):
        assert addr not in body


@pytest.mark.django_db
def test_member_still_gets_display_names(squad):
    team, u = squad
    members = _as(u["alice"]).get(f"/api/v1/teams/{team.id}/members/").json()
    names = {m["user"]["id"]: m["user"]["display_name"] for m in members}
    assert names[u["owner"].id] == "Olga" and names[u["manager"].id] == "Max"
    assert names[u["bob"].id] == ""  # no name: the client renders a neutral fallback


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["owner", "manager"])
def test_managers_see_every_email(squad, who):
    team, u = squad
    seen = _emails_seen(_as(u[who]), team)
    assert seen == {usr.id: usr.email for usr in u.values()}


@pytest.mark.django_db
def test_role_change_response_shows_email_to_manager(squad):
    team, u = squad
    resp = _as(u["manager"]).patch(
        f"/api/v1/teams/{team.id}/members/{u['bob'].id}/", {"role": "manager"}, format="json"
    )
    assert resp.status_code == 200 and resp.json()["user"]["email"] == "bob@example.com"


@pytest.mark.django_db
def test_team_payload_no_longer_carries_owner_email(squad):
    team, u = squad
    for who in ("alice", "owner"):
        detail = _as(u[who]).get(f"/api/v1/teams/{team.id}/").json()
        assert "owner_email" not in detail
        listed = _as(u[who]).get("/api/v1/teams/").json()
        assert all("owner_email" not in t for t in listed)
        assert "owner@example.com" not in _as(u[who]).get("/api/v1/teams/").content.decode()
