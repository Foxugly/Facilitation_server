import datetime

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

from rooms.models import Result, Room, RoundState, Subject, VoteSession
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


def _team_room(member, team):
    client = APIClient()
    client.force_authenticate(member)
    code = client.post("/api/v1/rooms", {"team": team.id}, format="json").json()["code"]
    return Room.objects.get(code=code)


def _act(room, text, value, seq):
    subject = Subject.objects.create(room=room, text=text, sequence=seq)
    session = VoteSession.objects.create(room=room, subject=subject, state=RoundState.ACTED)
    return Result.objects.create(session=session, subject=subject, chosen_value=value)


@pytest.mark.django_db
def test_history_list_and_detail(standard_deck, team, member):
    room = _team_room(member, team)
    _act(room, "Budget?", "5", 1)
    _act(room, "Hiring?", "3", 2)
    old = _act(room, "Roadmap?", "7", 3)
    # Force one result onto a previous day to exercise grouping/order.
    Result.objects.filter(pk=old.pk).update(
        decided_at=old.decided_at - datetime.timedelta(days=2)
    )

    client = APIClient()
    client.force_authenticate(member)

    days = client.get(f"/api/v1/history/{team.id}/").json()["days"]
    assert len(days) == 2
    assert days[0]["count"] == 2  # today, most recent first
    assert days[1]["count"] == 1  # two days ago

    today = days[0]["date"]
    detail = client.get(f"/api/v1/history/{team.id}/{today}/").json()
    assert detail["date"] == today
    assert [e["subject"] for e in detail["entries"]] == ["Budget?", "Hiring?"]
    advise = detail["entries"][0]
    assert advise["chosenValue"] == "5"
    assert advise["roomCode"] == room.code
    # The level NAME (not the number) is resolved from the room's deck snapshot.
    assert advise["levelName"]["fr"] == "Conseiller"


@pytest.mark.django_db
def test_history_forbidden_for_non_member(standard_deck, team, member):
    room = _team_room(member, team)
    _act(room, "Budget?", "5", 1)
    stranger = User.objects.create_user(email="x@example.com", password="pw12345678")
    client = APIClient()
    client.force_authenticate(stranger)
    assert client.get(f"/api/v1/history/{team.id}/").status_code == 403
    day = datetime.date.today().isoformat()
    assert client.get(f"/api/v1/history/{team.id}/{day}/").status_code == 403


@pytest.mark.django_db
def test_history_email_admin_only(standard_deck, team, member):
    room = _team_room(member, team)
    _act(room, "Budget?", "5", 1)
    bob = User.objects.create_user(email="bob@example.com", password="pw12345678", display_name="Bob")
    TeamMembership.objects.create(team=team, user=bob, role=TeamRole.MEMBER)
    day = datetime.date.today().isoformat()

    # A plain member cannot trigger the broadcast.
    bc = APIClient()
    bc.force_authenticate(bob)
    assert bc.post(f"/api/v1/history/{team.id}/{day}/email/").status_code == 403

    # The owner (admin) emails every member with an address.
    mail.outbox.clear()
    ac = APIClient()
    ac.force_authenticate(member)
    resp = ac.post(f"/api/v1/history/{team.id}/{day}/email/")
    assert resp.status_code == 200 and resp.json()["sent"] == 2
    assert len(mail.outbox) == 2
    assert {r for m in mail.outbox for r in m.to} == {"m@example.com", "bob@example.com"}


@pytest.mark.django_db
def test_history_email_empty_day_rejected(standard_deck, team, member):
    _team_room(member, team)
    day = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    ac = APIClient()
    ac.force_authenticate(member)
    assert ac.post(f"/api/v1/history/{team.id}/{day}/email/").status_code == 400


@pytest.mark.django_db
def test_history_detail_bad_date(standard_deck, team, member):
    _team_room(member, team)
    ac = APIClient()
    ac.force_authenticate(member)
    assert ac.get(f"/api/v1/history/{team.id}/not-a-date/").status_code == 400
