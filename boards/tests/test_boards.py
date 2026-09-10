import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from boards.models import Board, BoardRow
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="o@example.com", password="pw12345678", display_name="Owner")


@pytest.fixture
def team(owner):
    t = Team.objects.create(name="Squad", owner=owner)
    TeamMembership.objects.create(team=t, user=owner, role=TeamRole.OWNER)
    return t


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_get_board_autocreates_and_lists_seven_levels(standard_deck, team, owner):
    resp = _client(owner).get(f"/api/v1/board/{team.id}/")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["levels"]) == 7
    assert body["levels"][0]["value"] == "1"
    assert body["levels"][0]["name"]["fr"] == "Dire"
    assert body["rows"] == []
    assert Board.objects.filter(team=team).exists()


@pytest.mark.django_db
def test_board_forbidden_for_non_member(standard_deck, team):
    stranger = User.objects.create_user(email="s@example.com", password="pw12345678")
    assert _client(stranger).get(f"/api/v1/board/{team.id}/").status_code == 403


@pytest.mark.django_db
def test_row_crud_and_place_postits(standard_deck, team, owner):
    c = _client(owner)
    # create a row
    row = c.post(f"/api/v1/board/{team.id}/rows/", {"topic": "Budget"}, format="json").json()
    assert row["topic"] == "Budget" and row["asIs"] is None and row["toBe"] is None

    # place AS-IS on level 3 and TO-BE on level 5
    r = c.patch(f"/api/v1/board/{team.id}/rows/{row['id']}/", {"asIs": "3", "toBe": "5"}, format="json")
    assert r.status_code == 200 and r.json()["asIs"] == "3" and r.json()["toBe"] == "5"

    # clearing a post-it (null) is allowed
    r = c.patch(f"/api/v1/board/{team.id}/rows/{row['id']}/", {"asIs": None}, format="json")
    assert r.json()["asIs"] is None

    # an unknown level value is rejected
    assert c.patch(f"/api/v1/board/{team.id}/rows/{row['id']}/", {"toBe": "9"}, format="json").status_code == 400

    # delete
    assert c.delete(f"/api/v1/board/{team.id}/rows/{row['id']}/").status_code == 204
    assert not BoardRow.objects.filter(id=row["id"]).exists()


@pytest.mark.django_db
def test_row_edits_are_admin_only(standard_deck, team, owner):
    member = User.objects.create_user(email="m@example.com", password="pw12345678", display_name="Mia")
    TeamMembership.objects.create(team=team, user=member, role=TeamRole.MEMBER)
    mc = _client(member)
    # a plain member can read but not create
    assert mc.get(f"/api/v1/board/{team.id}/").status_code == 200
    assert mc.post(f"/api/v1/board/{team.id}/rows/", {"topic": "X"}, format="json").status_code == 403


@pytest.mark.django_db
def test_empty_topic_rejected(standard_deck, team, owner):
    assert _client(owner).post(f"/api/v1/board/{team.id}/rows/", {"topic": "  "}, format="json").status_code == 400
