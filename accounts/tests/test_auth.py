import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts.models import MagicLinkToken

User = get_user_model()


@pytest.fixture
def client():
    return APIClient()


def _register(client, email="sam@example.com", password="sup3rSecret!", name="Sam"):
    return client.post(
        "/api/v1/auth/register/",
        {"email": email, "password": password, "display_name": name},
        format="json",
    )


@pytest.mark.django_db
def test_register_creates_unconfirmed_user_without_tokens(client):
    resp = _register(client)
    assert resp.status_code == 201
    assert resp.json()["code"] == "registration_pending_verification"
    assert "access" not in resp.json()
    user = User.objects.get(email="sam@example.com")
    assert user.email_confirmed is False
    assert user.display_name == "Sam"


@pytest.mark.django_db
def test_login_blocked_until_email_confirmed_then_works(client):
    _register(client)
    resp = client.post("/api/v1/auth/login/", {"email": "sam@example.com", "password": "sup3rSecret!"}, format="json")
    assert resp.status_code == 403
    assert resp.json()["code"] == "email_not_verified"

    user = User.objects.get(email="sam@example.com")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    confirm = client.post("/api/v1/auth/email/confirm/", {"uid": uid, "token": token}, format="json")
    assert confirm.status_code == 200
    assert confirm.json()["access"] and confirm.json()["refresh"]
    assert confirm.json()["user"]["email_confirmed"] is True

    login = client.post("/api/v1/auth/login/", {"email": "sam@example.com", "password": "sup3rSecret!"}, format="json")
    assert login.status_code == 200
    assert login.json()["user"]["display_name"] == "Sam"


@pytest.mark.django_db
def test_register_duplicate_is_anti_enumeration(client):
    _register(client)
    resp = _register(client, name="Impostor")
    assert resp.status_code == 201  # same body — no leak
    assert User.objects.filter(email="sam@example.com").count() == 1


@pytest.mark.django_db
def test_forgot_and_reset_password(client):
    _register(client)
    user = User.objects.get(email="sam@example.com")
    forgot = client.post("/api/v1/auth/forgot-password/", {"email": "sam@example.com"}, format="json")
    assert forgot.status_code == 200  # anti-leak, always 200

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset = client.post(
        "/api/v1/auth/reset-password/",
        {"uid": uid, "token": token, "password": "brandN3wPass!"},
        format="json",
    )
    assert reset.status_code == 200
    # new password works + reset confirmed the email
    login = client.post("/api/v1/auth/login/", {"email": "sam@example.com", "password": "brandN3wPass!"}, format="json")
    assert login.status_code == 200


@pytest.mark.django_db
def test_magic_link_single_use(client):
    _register(client)
    user = User.objects.get(email="sam@example.com")
    user.email_confirmed = True
    user.save(update_fields=["email_confirmed"])

    req = client.post("/api/v1/auth/magic-link/", {"email": "sam@example.com"}, format="json")
    assert req.status_code == 200
    token = MagicLinkToken.objects.filter(user=user).latest("created_at").token

    ok = client.post("/api/v1/auth/magic-link/verify/", {"token": token}, format="json")
    assert ok.status_code == 200 and ok.json()["access"]
    # single use → second attempt rejected
    again = client.post("/api/v1/auth/magic-link/verify/", {"token": token}, format="json")
    assert again.status_code == 400


@pytest.mark.django_db
def test_refresh_rotates_and_me_endpoint(client):
    _register(client)
    user = User.objects.get(email="sam@example.com")
    user.email_confirmed = True
    user.save(update_fields=["email_confirmed"])
    login = client.post("/api/v1/auth/login/", {"email": "sam@example.com", "password": "sup3rSecret!"}, format="json").json()

    refreshed = client.post("/api/v1/auth/token/refresh/", {"refresh": login["refresh"]}, format="json")
    assert refreshed.status_code == 200
    assert refreshed.json()["access"]
    assert refreshed.json()["refresh"] != login["refresh"]  # rotation

    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login['access']}")
    me = client.get("/api/v1/auth/me/")
    assert me.status_code == 200 and me.json()["email"] == "sam@example.com"
    patched = client.patch("/api/v1/auth/me/", {"display_name": "Samuel"}, format="json")
    assert patched.status_code == 200 and patched.json()["display_name"] == "Samuel"
