"""Mistyped input is a 400, never a 500 (audit 2026-10-18, M1)."""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from boards.models import Board, BoardRow
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.fixture
def setup(db):
    user = User.objects.create_user(email="o@example.com", password="pw12345678", email_confirmed=True)
    team = Team.objects.create(name="T", owner=user)
    TeamMembership.objects.create(team=team, user=user, role=TeamRole.OWNER)
    client = APIClient()
    client.force_authenticate(user)
    return client, team


@pytest.mark.parametrize(
    "body",
    [
        {"name": 42},
        {"name": "x" * 121},
        {"deck_ids": ["a"]},
        {"deck_ids": [True]},
        {"deck_ids": [{"id": 1}]},
        {"felt_id": "abc"},
        {"felt_id": [1]},
        {"card_back_id": "abc"},
        {"background_id": {"x": 1}},
    ],
)
def test_team_patch_rejects_mistyped_fields(setup, body):
    client, team = setup
    resp = client.patch(f"/api/v1/teams/{team.id}/", body, format="json")
    assert resp.status_code == 400, resp.content
    team.refresh_from_db()
    assert team.name == "T"


def test_team_patch_still_accepts_a_valid_name(setup):
    client, team = setup
    assert client.patch(f"/api/v1/teams/{team.id}/", {"name": "  New  "}, format="json").status_code == 200
    team.refresh_from_db()
    assert team.name == "New"


@pytest.mark.parametrize(
    "body",
    [{"topic": 5}, {"topic": "x" * 201}, {"order": 2**31}, {"order": -1}, {"order": "1e9999"}],
)
def test_board_row_patch_rejects_out_of_range(setup, body):
    client, team = setup
    board = Board.objects.create(team=team)
    row = BoardRow.objects.create(board=board, topic="Hiring", order=0)
    resp = client.patch(f"/api/v1/board/{team.id}/rows/{row.id}/", body, format="json")
    assert resp.status_code == 400, resp.content


@pytest.mark.parametrize("topic", [5, "x" * 201])
def test_board_row_create_rejects_bad_topic(setup, topic):
    client, team = setup
    resp = client.post(f"/api/v1/board/{team.id}/rows/", {"topic": topic}, format="json")
    assert resp.status_code == 400, resp.content
