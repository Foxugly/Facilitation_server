"""GDPR: export one's own data - GET /api/v1/auth/account/export/."""
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from billing.models import Subscription
from decks.seed import create_standard_deck
from rooms.codes import generate_token
from rooms.models import Item, Participant, Response, Result, Role, Room, Round
from rooms.snapshot import build_deck_snapshot
from teams.models import Invitation, Team, TeamMembership, TeamRole

User = get_user_model()
URL = "/api/v1/auth/account/export/"


@pytest.fixture
def user(db):
    return User.objects.create_user(email="me@example.com", password="x", display_name="Me", email_confirmed=True)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _room(deck, team=None, code="ABCDEF"):
    room = Room(code=code, title="Sprint 12", vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck),
                team=team)
    room.touch(save=False)
    room.save()
    return room


def test_anonymous_is_refused(db):
    assert APIClient().get(URL).status_code == 401


def test_export_is_a_json_download(client):
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp["Content-Disposition"].startswith('attachment; filename="facilitation-export-')
    assert resp["Cache-Control"] == "no-store"
    body = resp.json()
    assert body["profile"]["email"] == "me@example.com"
    assert body["profile"]["display_name"] == "Me"
    assert body["subscription"] is None
    for key in ("team_memberships", "owned_teams", "invitations_sent", "invitations_received", "rooms", "uploads"):
        assert body[key] == []


def test_export_covers_the_callers_data_only(client, user):
    other = User.objects.create_user(email="other@example.com", password="x", display_name="Other")
    team = Team.objects.create(name="Squad", owner=user)
    TeamMembership.objects.create(team=team, user=user, role=TeamRole.OWNER)
    TeamMembership.objects.create(team=team, user=other, role=TeamRole.MEMBER)
    their_team = Team.objects.create(name="Theirs", owner=other)
    Invitation.objects.create(team=team, email="new@example.com", invited_by=user, expires_at="2099-01-01T00:00Z")
    Invitation.objects.create(team=their_team, email="ME@example.com", invited_by=other,
                              expires_at="2099-01-01T00:00Z")
    Invitation.objects.create(team=their_team, email="third@example.com", invited_by=other,
                              expires_at="2099-01-01T00:00Z")
    Subscription.objects.create(user=user, status="active", plan="team1", interval="monthly",
                                stripe_customer_id="cus_secret", is_paid=True)

    deck = create_standard_deck()
    room = _room(deck, team=team)
    me = Participant.objects.create(room=room, token=generate_token(), display_name="Me", role=Role.FACILITATOR,
                                    user=user)
    them = Participant.objects.create(room=room, token=generate_token(), display_name="Other", role=Role.VOTER,
                                      user=other)
    rnd = Round.objects.create(room=room, facilitator=me, sequence=1)
    agenda = Item.objects.create(round=rnd, text="Who deploys?", sequence=1)
    mine = Item.objects.create(round=rnd, text="My post-it", sequence=2, author=me)
    Item.objects.create(round=rnd, text="Their secret post-it", sequence=3, author=them)
    Response.objects.create(round=rnd, participant=me, item=agenda, payload={"card": "3"})
    Response.objects.create(round=rnd, participant=them, item=agenda, payload={"card": "7"})
    Response.objects.create(round=rnd, participant=them, item=mine, payload={"card": "6"})
    Result.objects.create(round=rnd, item=agenda, chosen_value="3", decided_by=me)

    body = client.get(URL).json()
    raw = str(body)

    assert [m["team"] for m in body["team_memberships"]] == ["Squad"]
    assert [t["name"] for t in body["owned_teams"]] == ["Squad"]
    assert [i["email"] for i in body["invitations_sent"]] == ["new@example.com"]
    assert [i["team"] for i in body["invitations_received"]] == ["Theirs"]
    assert body["subscription"]["plan"] == "team1"
    [seat] = body["rooms"]
    assert seat["room_code"] == "ABCDEF" and seat["role"] == "facilitator"
    assert [(r["round"], r["item"], r["payload"]) for r in seat["responses"]] == [(1, "Who deploys?", {"card": "3"})]
    assert [i["text"] for i in seat["items_authored"]] == ["My post-it"]
    # Nothing that belongs to someone else, nor any secret.
    assert "other@example.com" not in raw
    assert "third@example.com" not in raw
    assert "'7'" not in raw and "'6'" not in raw
    assert "Their secret post-it" not in raw
    assert them.token not in raw and me.token not in raw
    assert "cus_secret" not in raw
    assert user.password not in raw


def test_voter_seat_without_contribution(client, user):
    room = _room(create_standard_deck())
    Participant.objects.create(room=room, token=generate_token(), display_name="Me", role=Role.VOTER, user=user)
    [seat] = client.get(URL).json()["rooms"]
    assert seat["role"] == "voter"
    assert seat["responses"] == [] and seat["items_authored"] == []


def test_export_is_throttled(client, monkeypatch):
    from accounts.throttles import AccountExportRateThrottle

    monkeypatch.setattr(AccountExportRateThrottle, "rate", "2/hour", raising=False)
    cache.clear()
    try:
        assert client.get(URL).status_code == 200
        assert client.get(URL).status_code == 200
        assert client.get(URL).status_code == 429
    finally:
        cache.clear()
