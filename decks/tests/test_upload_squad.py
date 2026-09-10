"""User-uploaded card backs / felts, visible through the uploader's squad."""
import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from decks.models import CardBack, Felt
from decks.selection import available_card_backs, available_felts, can_upload, squad_of
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _user(email):
    return User.objects.create_user(email=email, password="pw12345678", display_name=email.split("@")[0])


def _png(size=(40, 40), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 90)).save(buf, format=fmt)
    buf.seek(0)
    ct = {"PNG": "image/png", "WEBP": "image/webp", "JPEG": "image/jpeg"}[fmt]
    return SimpleUploadedFile("up." + fmt.lower(), buf.read(), content_type=ct)


@pytest.fixture
def owner(db):
    return _user("o1@example.com")


@pytest.fixture
def team(db, owner):
    t = Team.objects.create(name="Acme", owner=owner)
    TeamMembership.objects.create(team=t, user=owner, role=TeamRole.OWNER)
    return t


def _auth(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


# --- squad + gating ------------------------------------------------------------

@pytest.mark.django_db
def test_squad_is_owner_plus_managers_across_the_owners_teams(owner):
    managers = [_user(f"m{i}@example.com") for i in range(3)]
    member = _user("plain@example.com")
    for i, m in enumerate(managers):
        t = Team.objects.create(name=f"T{i}", owner=owner)
        TeamMembership.objects.create(team=t, user=owner, role=TeamRole.OWNER)
        TeamMembership.objects.create(team=t, user=m, role=TeamRole.MANAGER)
    TeamMembership.objects.create(
        team=Team.objects.first(), user=member, role=TeamRole.MEMBER
    )

    squad = squad_of(owner)
    assert squad == {owner.pk, *[m.pk for m in managers]}
    assert member.pk not in squad


@pytest.mark.django_db
def test_only_owner_or_manager_may_upload(team, owner):
    assert can_upload(owner) is True

    manager = _user("mgr@example.com")
    TeamMembership.objects.create(team=team, user=manager, role=TeamRole.MANAGER)
    assert can_upload(manager) is True

    member = _user("mem@example.com")
    TeamMembership.objects.create(team=team, user=member, role=TeamRole.MEMBER)
    assert can_upload(member) is False

    assert can_upload(_user("nobody@example.com")) is False


@pytest.mark.django_db
def test_member_upload_is_forbidden(team):
    member = _user("mem@example.com")
    TeamMembership.objects.create(team=team, user=member, role=TeamRole.MEMBER)

    resp = _auth(member).post("/api/v1/decks/card-backs/", {"name": "x", "image": _png()}, format="multipart")
    assert resp.status_code == 403


# --- upload + visibility -------------------------------------------------------

@pytest.mark.django_db
def test_owner_uploads_a_custom_back_and_the_team_sees_it(team, owner):
    resp = _auth(owner).post(
        "/api/v1/decks/card-backs/", {"name": "Mon dos", "image": _png()}, format="multipart"
    )
    assert resp.status_code == 201
    back_id = resp.json()["id"]
    assert resp.json()["is_custom"] is True

    back = CardBack.objects.get(pk=back_id)
    assert (back.uploaded_by_id, back.is_standard, back.free_tier) == (owner.pk, False, False)
    assert back_id in [b.pk for b in available_card_backs(team)]


@pytest.mark.django_db
def test_a_managers_upload_is_visible_to_the_owners_team(team, owner):
    manager = _user("mgr@example.com")
    other = Team.objects.create(name="Other", owner=owner)
    TeamMembership.objects.create(team=other, user=owner, role=TeamRole.OWNER)
    TeamMembership.objects.create(team=other, user=manager, role=TeamRole.MANAGER)

    mine = _auth(manager).post("/api/v1/decks/felts/", {"name": "M", "image": _png()}, format="multipart").json()

    # Visible to `team` (same owner o1), because the manager is in o1's squad.
    assert mine["id"] in [f.pk for f in available_felts(team)]


@pytest.mark.django_db
def test_a_strangers_upload_is_not_visible(team):
    stranger_team_owner = _user("ox@example.com")
    st = Team.objects.create(name="Strangers", owner=stranger_team_owner)
    TeamMembership.objects.create(team=st, user=stranger_team_owner, role=TeamRole.OWNER)
    theirs = _auth(stranger_team_owner).post(
        "/api/v1/decks/card-backs/", {"name": "Theirs", "image": _png()}, format="multipart"
    ).json()

    assert theirs["id"] not in [b.pk for b in available_card_backs(team)]


@pytest.mark.django_db
def test_builtins_stay_visible_to_everyone(team, standard_deck):
    # A built-in back (is_standard=True, no uploader) shows regardless of squad.
    builtin = CardBack.objects.create(is_standard=True, name="House", image="decks/backs/house.png")
    assert builtin.pk in [b.pk for b in available_card_backs(team)]


# --- validation ----------------------------------------------------------------

@pytest.mark.django_db
def test_a_non_image_is_rejected(team, owner):
    bad = SimpleUploadedFile("evil.png", b"not an image", content_type="image/png")
    resp = _auth(owner).post("/api/v1/decks/card-backs/", {"name": "x", "image": bad}, format="multipart")
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_image"


@pytest.mark.django_db
def test_an_oversized_image_is_rejected(team, owner, settings):
    from decks import validators

    # A 20 MB payload is over the 3 MB cap regardless of pixels.
    big = SimpleUploadedFile("big.png", b"\x89PNG\r\n" + b"0" * (20 * 1024 * 1024), content_type="image/png")
    resp = _auth(owner).post("/api/v1/decks/card-backs/", {"name": "x", "image": big}, format="multipart")
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_image"


# --- deletion + orphaning ------------------------------------------------------

@pytest.mark.django_db
def test_only_the_uploader_can_delete(team, owner):
    back = CardBack.objects.create(is_standard=False, uploaded_by=owner, name="Mine", image="decks/backs/x.png")
    other = _user("other@example.com")

    assert _auth(other).delete(f"/api/v1/decks/card-backs/{back.pk}/").status_code == 403
    assert _auth(owner).delete(f"/api/v1/decks/card-backs/{back.pk}/").status_code == 204
    assert not CardBack.objects.filter(pk=back.pk).exists()


@pytest.mark.django_db
def test_an_orphaned_upload_is_visible_to_nobody(team, owner):
    """Uploader deleted → uploaded_by null, but is_standard stays False, so the
    entry does NOT become a global built-in."""
    manager = _user("mgr@example.com")
    TeamMembership.objects.create(team=team, user=manager, role=TeamRole.MANAGER)
    back = CardBack.objects.create(is_standard=False, uploaded_by=manager, name="Orphan", image="decks/backs/x.png")
    assert back.pk in [b.pk for b in available_card_backs(team)]

    manager.delete()
    back.refresh_from_db()
    assert back.uploaded_by_id is None
    assert back.is_standard is False
    assert back.pk not in [b.pk for b in available_card_backs(team)]
