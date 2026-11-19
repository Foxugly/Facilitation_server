"""The admin 2FA (admin_2fa/) must not change anything for API users, staff included."""
import pytest
from django.contrib.auth import get_user_model
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

PASSWORD = "sup3rSecret!"


@pytest.mark.django_db
@pytest.mark.parametrize("enrolled", [False, True])
def test_staff_uses_the_api_with_a_password_only(enrolled):
    user = get_user_model().objects.create_user(email="ops@example.com", password=PASSWORD)
    user.email_confirmed = True
    user.is_staff = True
    user.save()
    if enrolled:
        TOTPDevice.objects.create(user=user, name="admin-2fa", confirmed=True)

    client = APIClient()
    login = client.post("/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    me = client.get("/api/v1/auth/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "ops@example.com"


@pytest.mark.django_db
def test_health_stays_public():
    assert APIClient().get("/health/").status_code == 200
