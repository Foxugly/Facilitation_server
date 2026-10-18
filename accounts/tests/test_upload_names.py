"""Uploaded images never keep the client's file name (audit 2026-10-18, I2).

Pillow already checks the bytes; the stored name used to be the client's, so a
valid PNG followed by HTML and named ``x.html`` was served as text/html from the
API origin. The name is now random and its extension follows the detected type.
"""
import io
import os
import shutil
import tempfile

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _polyglot(name="x.html", fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 10, 10)).save(buf, format=fmt)
    return SimpleUploadedFile(name, buf.getvalue() + b"<script>alert(1)</script>", content_type="text/html")


@pytest.fixture(autouse=True)
def _media(settings):
    root = tempfile.mkdtemp(prefix="facilitation-media-")
    settings.MEDIA_ROOT = root
    yield
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="u@example.com", password="pw12345678", email_confirmed=True)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_avatar_name_is_random_with_detected_extension(client, user):
    resp = client.post("/api/v1/auth/me/avatar/", {"image": _polyglot("x.html")}, format="multipart")
    assert resp.status_code == 200
    user.refresh_from_db()
    base = os.path.basename(user.avatar.name)
    assert base.endswith(".png")
    assert "x" != base.split(".")[0] and len(base.split(".")[0]) == 32
    assert resp.json()["avatar_url"].endswith(".png")


@pytest.mark.django_db
def test_jpeg_named_png_is_stored_as_jpg(client, user):
    resp = client.post("/api/v1/auth/me/avatar/", {"image": _polyglot("me.png", fmt="JPEG")}, format="multipart")
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.avatar.name.endswith(".jpg")


@pytest.mark.django_db
def test_replacing_the_avatar_deletes_the_previous_file(client, user):
    client.post("/api/v1/auth/me/avatar/", {"image": _polyglot("a.png")}, format="multipart")
    user.refresh_from_db()
    first = user.avatar.path
    assert os.path.exists(first)
    client.post("/api/v1/auth/me/avatar/", {"image": _polyglot("b.png")}, format="multipart")
    user.refresh_from_db()
    assert user.avatar.path != first
    assert not os.path.exists(first)


@pytest.mark.django_db
def test_gif_is_refused(client):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="GIF")
    resp = client.post(
        "/api/v1/auth/me/avatar/",
        {"image": SimpleUploadedFile("a.png", buf.getvalue(), content_type="image/png")},
        format="multipart",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/v1/decks/card-backs/", "/api/v1/decks/felts/", "/api/v1/decks/backgrounds/"])
def test_catalogue_upload_name_is_random(client, user, path):
    team = Team.objects.create(name="T", owner=user)
    TeamMembership.objects.create(team=team, user=user, role=TeamRole.OWNER)
    resp = client.post(path, {"image": _polyglot("evil.html"), "name": "x"}, format="multipart")
    assert resp.status_code == 201, resp.content
    assert resp.json()["image"].endswith(".png")
    assert "evil" not in resp.json()["image"]
