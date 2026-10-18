"""Token hardening (audit 2026-10-18, I3 + I4 + M5).

- The e-mail confirmation link has its own generator: single use, never a
  password-reset key (and vice versa), and never a JWT pair twice.
- A password reset revokes every refresh token and every pending magic link.
- A magic link is consumed atomically (conditional UPDATE, no read-then-save).
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts.models import MagicLinkToken
from accounts.tokens import email_confirmation_token_generator

User = get_user_model()
PASSWORD = "sup3rSecret!"


@pytest.fixture
def client():
    return APIClient()


def _uid(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


@pytest.fixture
def pending(db):
    return User.objects.create_user(email="p@example.com", password=PASSWORD)


@pytest.fixture
def confirmed(db):
    return User.objects.create_user(email="c@example.com", password=PASSWORD, email_confirmed=True)


@pytest.mark.django_db
def test_confirmation_link_is_single_use(client, pending):
    token = email_confirmation_token_generator.make_token(pending)
    first = client.post("/api/v1/auth/email/confirm/", {"uid": _uid(pending), "token": token}, format="json")
    assert first.status_code == 200 and first.json()["access"]
    again = client.post("/api/v1/auth/email/confirm/", {"uid": _uid(pending), "token": token}, format="json")
    assert again.status_code == 400
    assert "access" not in again.json()


@pytest.mark.django_db
def test_confirmation_token_is_not_a_reset_key(client, pending):
    token = email_confirmation_token_generator.make_token(pending)
    resp = client.post(
        "/api/v1/auth/reset-password/",
        {"uid": _uid(pending), "token": token, "password": "brandN3wPass!"},
        format="json",
    )
    assert resp.status_code == 400
    pending.refresh_from_db()
    assert pending.check_password(PASSWORD)


@pytest.mark.django_db
def test_reset_token_is_not_a_confirmation_key(client, pending):
    token = default_token_generator.make_token(pending)
    resp = client.post("/api/v1/auth/email/confirm/", {"uid": _uid(pending), "token": token}, format="json")
    assert resp.status_code == 400
    pending.refresh_from_db()
    assert pending.email_confirmed is False


@pytest.mark.django_db
def test_confirmed_account_gets_no_jwt_from_a_confirmation_link(client, confirmed):
    token = email_confirmation_token_generator.make_token(confirmed)
    resp = client.post("/api/v1/auth/email/confirm/", {"uid": _uid(confirmed), "token": token}, format="json")
    assert resp.status_code == 400
    assert "access" not in resp.json()


@pytest.mark.django_db
def test_sent_confirmation_link_uses_the_dedicated_generator(client, mailoutbox):
    client.post(
        "/api/v1/auth/register/", {"email": "new@example.com", "password": PASSWORD, "display_name": "N"},
        format="json",
    )
    user = User.objects.get(email="new@example.com")
    body = mailoutbox[-1].body
    token = body.split("/auth/confirm-email/")[1].split()[0].split("/")[1]
    assert email_confirmation_token_generator.check_token(user, token)
    assert not default_token_generator.check_token(user, token)


def _login(client, email):
    resp = client.post("/api/v1/auth/login/", {"email": email, "password": PASSWORD}, format="json")
    assert resp.status_code == 200
    return resp.json()["refresh"]


@pytest.mark.django_db
def test_password_reset_revokes_refresh_tokens(client, confirmed):
    refresh = _login(client, confirmed.email)
    token = default_token_generator.make_token(confirmed)
    reset = client.post(
        "/api/v1/auth/reset-password/",
        {"uid": _uid(confirmed), "token": token, "password": "brandN3wPass!"},
        format="json",
    )
    assert reset.status_code == 200
    resp = client.post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_password_reset_kills_pending_magic_links(client, confirmed):
    link = MagicLinkToken.objects.create(user=confirmed, expires_at=timezone.now() + timezone.timedelta(minutes=10))
    token = default_token_generator.make_token(confirmed)
    client.post(
        "/api/v1/auth/reset-password/",
        {"uid": _uid(confirmed), "token": token, "password": "brandN3wPass!"},
        format="json",
    )
    resp = client.post("/api/v1/auth/magic-link/verify/", {"token": link.token}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_magic_link_is_consumed_by_a_conditional_update(confirmed):
    from accounts.magic_link import verify_magic_link

    link = MagicLinkToken.objects.create(user=confirmed, expires_at=timezone.now() + timezone.timedelta(minutes=10))
    assert verify_magic_link(link.token) == confirmed
    assert verify_magic_link(link.token) is None


@pytest.mark.django_db
def test_magic_link_of_a_deactivated_account_is_refused(confirmed):
    from accounts.magic_link import verify_magic_link

    link = MagicLinkToken.objects.create(user=confirmed, expires_at=timezone.now() + timezone.timedelta(minutes=10))
    User.objects.filter(pk=confirmed.pk).update(is_active=False)
    assert verify_magic_link(link.token) is None
