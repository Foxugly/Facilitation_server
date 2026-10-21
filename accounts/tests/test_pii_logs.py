"""E-mail addresses never reach the logs in clear (audit 2026-10-18, M2)."""
import logging

import pytest
from django.contrib.auth import get_user_model

from accounts.mail import send_email
from accounts.pii import mask_email
from teams.invitations import send_invitation_email
from teams.models import Invitation, Team

User = get_user_model()
ADDRESS = "john.doe@example.com"


def _logged(caplog):
    return " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)


def test_mask_email():
    assert mask_email(ADDRESS) == "j***@example.com"
    assert mask_email("nope") == "***"
    assert mask_email(None) == "***"


def test_send_email_logs_a_masked_address(caplog):
    with caplog.at_level(logging.INFO):
        send_email(to=ADDRESS, subject="Hi", body="Body")
    assert "email_sent" in _logged(caplog)
    assert ADDRESS not in _logged(caplog)


def test_failed_send_logs_a_masked_address(caplog, monkeypatch):
    from accounts import mail

    def boom(*_a, **_kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mail, "send_mail", boom)
    with caplog.at_level(logging.INFO):
        send_email(to=ADDRESS, subject="Hi", body="Body")
    assert "email_send_failed" in _logged(caplog)
    assert ADDRESS not in _logged(caplog)


@pytest.mark.django_db
def test_invitation_log_carries_ids_not_the_address(caplog):
    from django.utils import timezone

    owner = User.objects.create_user(email="o@example.com", password="pw12345678")
    team = Team.objects.create(name="T", owner=owner)
    invitation = Invitation.objects.create(team=team, email=ADDRESS, expires_at=timezone.now())
    with caplog.at_level(logging.INFO):
        send_invitation_email(invitation)
    assert "team_invitation_sent" in _logged(caplog)
    assert ADDRESS not in _logged(caplog)
