import datetime

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from teams.models import Invitation, Team, TeamMembership, TeamRole

User = get_user_model()


def _user(email, name=""):
    return User.objects.create_user(email=email, password="pw12345678", display_name=name)


@pytest.fixture
def owner(db):
    return _user("owner@example.com", "Owner")


@pytest.fixture
def client(owner):
    c = APIClient()
    c.force_authenticate(owner)
    return c


def _create_team(client, name="Acme"):
    return client.post("/api/v1/teams/", {"name": name}, format="json")


@pytest.mark.django_db
def test_create_team_makes_creator_owner(client, owner):
    resp = _create_team(client)
    assert resp.status_code == 201
    assert resp.json()["my_role"] == TeamRole.OWNER
    assert resp.json()["member_count"] == 1
    team = Team.objects.get(pk=resp.json()["id"])
    assert TeamMembership.objects.get(team=team, user=owner).role == TeamRole.OWNER


@pytest.mark.django_db
def test_list_only_my_teams(client, owner):
    _create_team(client, "Mine")
    other = _user("other@example.com")
    Team.objects.create(name="Theirs", owner=other)  # no membership for owner
    data = client.get("/api/v1/teams/").json()
    assert [t["name"] for t in data] == ["Mine"]


@pytest.mark.django_db
def test_invite_creates_pending_and_sends_email(client, owner):
    team_id = _create_team(client).json()["id"]
    resp = client.post(f"/api/v1/teams/{team_id}/invitations/", {"email": "New@Example.com", "role": "member"}, format="json")
    assert resp.status_code == 201
    inv = Invitation.objects.get(team_id=team_id)
    assert inv.email == "new@example.com" and inv.is_pending
    assert len(mail.outbox) == 1 and "new@example.com" in mail.outbox[0].to


@pytest.mark.django_db
def test_accept_invitation_adds_member(client, owner):
    team_id = _create_team(client).json()["id"]
    client.post(f"/api/v1/teams/{team_id}/invitations/", {"email": "bob@example.com"}, format="json")
    token = Invitation.objects.get(team_id=team_id).token

    bob = _user("bob@example.com", "Bob")
    bob_client = APIClient()
    bob_client.force_authenticate(bob)
    resp = bob_client.post("/api/v1/teams/invitations/accept/", {"token": token}, format="json")
    assert resp.status_code == 200
    assert TeamMembership.objects.filter(team_id=team_id, user=bob, role=TeamRole.MEMBER).exists()
    assert Invitation.objects.get(team_id=team_id).accepted_at is not None


@pytest.mark.django_db
def test_accept_with_wrong_email_is_forbidden(client, owner):
    team_id = _create_team(client).json()["id"]
    client.post(f"/api/v1/teams/{team_id}/invitations/", {"email": "bob@example.com"}, format="json")
    token = Invitation.objects.get(team_id=team_id).token

    intruder = _user("intruder@example.com")
    ic = APIClient()
    ic.force_authenticate(intruder)
    resp = ic.post("/api/v1/teams/invitations/accept/", {"token": token}, format="json")
    assert resp.status_code == 403 and resp.json()["code"] == "invite_email_mismatch"


@pytest.mark.django_db
def test_member_cannot_invite_but_manager_can(client, owner):
    team_id = _create_team(client).json()["id"]
    member = _user("member@example.com")
    TeamMembership.objects.create(team_id=team_id, user=member, role=TeamRole.MEMBER)

    mc = APIClient()
    mc.force_authenticate(member)
    assert mc.post(f"/api/v1/teams/{team_id}/invitations/", {"email": "x@example.com"}, format="json").status_code == 403

    # promote to manager → can invite
    client.patch(f"/api/v1/teams/{team_id}/members/{member.id}/", {"role": "manager"}, format="json")
    assert mc.post(f"/api/v1/teams/{team_id}/invitations/", {"email": "y@example.com"}, format="json").status_code == 201


@pytest.mark.django_db
def test_non_member_cannot_view_team(client, owner):
    team_id = _create_team(client).json()["id"]
    stranger = _user("stranger@example.com")
    sc = APIClient()
    sc.force_authenticate(stranger)
    assert sc.get(f"/api/v1/teams/{team_id}/").status_code == 403


@pytest.mark.django_db
def test_team_member_cap_blocks_accept(client, owner, settings):
    settings.TEAM_MAX_MEMBERS = 1  # owner alone already fills the team
    team_id = _create_team(client).json()["id"]
    bob = _user("bob@example.com", "Bob")
    inv = Invitation.objects.create(
        team_id=team_id, email="bob@example.com", role=TeamRole.MEMBER, invited_by=owner,
        expires_at=timezone.now() + datetime.timedelta(days=1),
    )
    bc = APIClient()
    bc.force_authenticate(bob)
    resp = bc.post("/api/v1/teams/invitations/accept/", {"token": inv.token}, format="json")
    assert resp.status_code == 403 and resp.json()["code"] == "team_full"


@pytest.mark.django_db
def test_appearance_colors_admin_only_and_validated(client, owner):
    team_id = _create_team(client).json()["id"]

    # Owner (admin) sets valid colours.
    r = client.patch(f"/api/v1/teams/{team_id}/", {"card_back_color": "#222222", "felt_color": "#0abf53"}, format="json")
    assert r.status_code == 200
    assert r.json()["card_back_color"] == "#222222" and r.json()["felt_color"] == "#0abf53"

    # An invalid hex is rejected.
    assert client.patch(f"/api/v1/teams/{team_id}/", {"felt_color": "green"}, format="json").status_code == 400

    # A plain member cannot change appearance.
    member = _user("m@example.com", "Mia")
    TeamMembership.objects.create(team_id=team_id, user=member, role=TeamRole.MEMBER)
    mc = APIClient()
    mc.force_authenticate(member)
    assert mc.patch(f"/api/v1/teams/{team_id}/", {"felt_color": "#ffffff"}, format="json").status_code == 403


@pytest.mark.django_db
def test_owner_cannot_be_removed(client, owner):
    team_id = _create_team(client).json()["id"]
    resp = client.delete(f"/api/v1/teams/{team_id}/members/{owner.id}/")
    assert resp.status_code == 400 and resp.json()["code"] == "cannot_remove_owner"


@pytest.mark.django_db
def test_existing_admins_became_managers(client, owner):
    """Guard for migration 0008: the rename must not silently demote anyone.

    Writing the old value straight to the DB simulates a row created before the
    rename; it must no longer grant anything.
    """
    team_id = _create_team(client).json()["id"]
    member = _user("legacy@example.com")
    TeamMembership.objects.create(team_id=team_id, user=member, role=TeamRole.MANAGER)

    mc = APIClient()
    mc.force_authenticate(member)
    assert mc.post(f"/api/v1/teams/{team_id}/invitations/", {"email": "z@example.com"}, format="json").status_code == 201

    TeamMembership.objects.filter(team_id=team_id, user=member).update(role="admin")
    assert mc.post(f"/api/v1/teams/{team_id}/invitations/", {"email": "w@example.com"}, format="json").status_code == 403
