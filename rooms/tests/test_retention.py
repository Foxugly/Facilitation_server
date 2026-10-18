"""Daily retention purge (audit 2026-10-18, I6).

Anonymous rooms (display names, votes) are deleted RETENTION_ROOM_DAYS after
their last activity; team rooms feed the team history and live as long as the
team. Dead sign-in links, expired refresh-token rows and old invitations go too.
"""
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import MagicLinkToken
from decks.seed import create_standard_deck
from rooms.codes import generate_token
from rooms.models import Item, Participant, Response, Result, Role, Room, Round, RoundState
from rooms.snapshot import build_deck_snapshot
from rooms.tasks import purge_retention
from teams.models import Invitation, Team

User = get_user_model()


def _room(code, *, team=None, idle_days=0):
    deck = create_standard_deck()
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), team=team)
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    rnd = Round.objects.create(room=room, state=RoundState.ACTED, facilitator=fac)
    item = Item.objects.create(round=rnd, text="Topic")
    Response.objects.create(round=rnd, participant=fac, item=item, payload={"card": "3"})
    Result.objects.create(round=rnd, item=item, chosen_value="3", decided_by=fac)
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    past = timezone.now() - timezone.timedelta(days=idle_days)
    Room.objects.filter(pk=room.pk).update(last_activity_at=past, expires_at=past + timezone.timedelta(hours=8))
    return room


@pytest.mark.django_db
def test_old_anonymous_rooms_are_deleted_with_their_people(settings):
    settings.RETENTION_ROOM_DAYS = 30
    old = _room("OLDAAA", idle_days=31)
    recent = _room("NEWAAA", idle_days=5)
    purge_retention()
    assert not Room.objects.filter(pk=old.pk).exists()
    assert not Participant.objects.filter(room_id=old.pk).exists()
    assert Room.objects.filter(pk=recent.pk).exists()


@pytest.mark.django_db
def test_team_rooms_are_kept_for_the_history(settings):
    settings.RETENTION_ROOM_DAYS = 30
    owner = User.objects.create_user(email="o@example.com", password="pw12345678")
    team = Team.objects.create(name="T", owner=owner)
    room = _room("TEAMAA", team=team, idle_days=400)
    purge_retention()
    assert Room.objects.filter(pk=room.pk).exists()


@pytest.mark.django_db
def test_stale_tokens_and_invitations_are_purged(settings):
    settings.RETENTION_INVITATION_DAYS = 30
    owner = User.objects.create_user(email="o@example.com", password="pw12345678")
    team = Team.objects.create(name="T", owner=owner)
    now = timezone.now()
    dead_link = MagicLinkToken.objects.create(user=owner, expires_at=now - timezone.timedelta(days=2))
    live_link = MagicLinkToken.objects.create(user=owner, expires_at=now + timezone.timedelta(minutes=5))
    old_inv = Invitation.objects.create(team=team, email="a@example.com", expires_at=now - timezone.timedelta(days=31))
    new_inv = Invitation.objects.create(team=team, email="b@example.com", expires_at=now + timezone.timedelta(days=3))
    purge_retention()
    assert not MagicLinkToken.objects.filter(pk=dead_link.pk).exists()
    assert MagicLinkToken.objects.filter(pk=live_link.pk).exists()
    assert not Invitation.objects.filter(pk=old_inv.pk).exists()
    assert Invitation.objects.filter(pk=new_inv.pk).exists()


def test_purge_runs_daily_in_beat():
    from django.conf import settings

    entries = [e for e in settings.CELERY_BEAT_SCHEDULE.values() if e["task"] == "rooms.tasks.purge_retention"]
    assert len(entries) == 1
