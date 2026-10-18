"""Authenticated users are throttled too (audit 2026-10-18, I5).

DEFAULT_THROTTLE_CLASSES used to hold AnonRateThrottle only: with a Bearer token
nothing was limited, and 40 invitations sent 40 e-mails. The test settings lift
every rate to an unreachable value, so each test pins the rate it checks.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.settings import api_settings
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.fixture
def rates(monkeypatch):
    cache.clear()
    current = dict(api_settings.DEFAULT_THROTTLE_RATES)
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", current)
    yield current
    cache.clear()


@pytest.fixture
def manager(db):
    user = User.objects.create_user(email="mgr@example.com", password="pw12345678", email_confirmed=True)
    team = Team.objects.create(name="T", owner=user)
    TeamMembership.objects.create(team=team, user=user, role=TeamRole.OWNER)
    client = APIClient()
    client.force_authenticate(user)
    return client, team


def test_user_rate_is_a_default_throttle():
    from django.conf import settings

    classes = [c.rsplit(".", 1)[-1] for c in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]]
    assert "UserRateThrottle" in classes and "AnonRateThrottle" in classes


@pytest.mark.django_db
def test_authenticated_requests_hit_the_user_rate(rates, manager):
    client, team = manager
    rates["user"] = "3/min"
    codes = [client.get(f"/api/v1/teams/{team.id}/").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


@pytest.mark.django_db
def test_invitations_have_their_own_scope(rates, manager, mailoutbox):
    client, team = manager
    rates["invite"] = "2/hour"
    codes = [
        client.post(f"/api/v1/teams/{team.id}/invitations/", {"email": f"x{i}@example.com"}, format="json").status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]
    assert len(mailoutbox) == 2
    # Reading the pending list is not an invitation.
    assert client.get(f"/api/v1/teams/{team.id}/invitations/").status_code == 200


@pytest.mark.django_db
def test_history_email_has_its_own_scope(rates, manager):
    client, team = manager
    rates["history_email"] = "1/hour"
    first = client.post(f"/api/v1/history/{team.id}/2026-01-01/email/")
    second = client.post(f"/api/v1/history/{team.id}/2026-01-01/email/")
    assert first.status_code != 429
    assert second.status_code == 429


@pytest.mark.django_db
def test_avatar_upload_has_its_own_scope(rates, manager):
    client, _team = manager
    rates["upload"] = "1/hour"
    first = client.post("/api/v1/auth/me/avatar/", {}, format="multipart")
    second = client.post("/api/v1/auth/me/avatar/", {}, format="multipart")
    assert first.status_code == 400  # no image, but counted
    assert second.status_code == 429
