"""Avatar upload on /api/auth/me/avatar/."""
import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

User = get_user_model()


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), (30, 120, 90)).save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile("me.png", buf.read(), content_type="image/png")


@pytest.fixture
def user(db):
    return User.objects.create_user(email="u@example.com", password="pw12345678", display_name="U", email_confirmed=True)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_anonymous_cannot_upload():
    assert APIClient().post("/api/v1/auth/me/avatar/", {}, format="multipart").status_code == 401


@pytest.mark.django_db
def test_upload_sets_avatar_url_absolute(client, user, settings):
    settings.PUBLIC_MEDIA_BASE_URL = "https://media.example"
    resp = client.post("/api/v1/auth/me/avatar/", {"image": _png()}, format="multipart")

    assert resp.status_code == 200
    url = resp.json()["avatar_url"]
    assert url.startswith("https://media.example/media/avatars/")
    user.refresh_from_db()
    assert user.avatar.name.startswith("avatars/")


@pytest.mark.django_db
def test_me_exposes_avatar_url_empty_by_default(client):
    assert client.get("/api/v1/auth/me/").json()["avatar_url"] == ""


@pytest.mark.django_db
def test_a_non_image_is_rejected(client):
    bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    resp = client.post("/api/v1/auth/me/avatar/", {"image": bad}, format="multipart")
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_image"


@pytest.mark.django_db
def test_delete_clears_the_avatar(client, user):
    client.post("/api/v1/auth/me/avatar/", {"image": _png()}, format="multipart")
    resp = client.delete("/api/v1/auth/me/avatar/")
    assert resp.status_code == 200
    assert resp.json()["avatar_url"] == ""
    user.refresh_from_db()
    assert not user.avatar
