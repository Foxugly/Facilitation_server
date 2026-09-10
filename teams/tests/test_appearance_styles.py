"""Appearance styles: each surface says whether it renders a colour or an image.

Before the discriminator a team could hold both a colour and a card back with
nothing deciding which applied — in practice both were sent. These pin that the
style now decides, and that the other representation is still carried so
switching back needs no new room.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from decks.models import CardBack, Felt
from teams.models import SurfaceStyle, Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="o@example.com", password="pw12345678", display_name="O")


@pytest.fixture
def team(db, owner):
    t = Team.objects.create(name="Acme", owner=owner)
    TeamMembership.objects.create(team=t, user=owner, role=TeamRole.OWNER)
    return t


@pytest.fixture
def client(owner):
    c = APIClient()
    c.force_authenticate(owner)
    return c


@pytest.fixture
def felt(db):
    return Felt.objects.create(is_standard=True, image="decks/felts/wood.webp", name="Wood")


@pytest.fixture
def back(db):
    return CardBack.objects.create(is_standard=True, image="decks/backs/blue.webp", name="Blue")


def _snapshot(client, team):
    resp = client.post("/api/v1/rooms", {"title": "Retro", "team": team.pk}, format="json")
    assert resp.status_code == 201
    return resp.json()["deckSnapshot"]


@pytest.mark.django_db
def test_default_is_colour_on_both_surfaces(client, team, standard_deck):
    snap = _snapshot(client, team)

    assert snap["cardBack"]["style"] == "color"
    assert snap["cardBack"]["color"] == team.card_back_color
    assert snap["felt"]["style"] == "color"
    assert snap["felt"]["color"] == team.felt_color


@pytest.mark.django_db
def test_image_style_uses_the_picked_back_and_felt(client, team, standard_deck, back, felt):
    team.card_back_style = SurfaceStyle.IMAGE
    team.card_back = back
    team.felt_style = SurfaceStyle.IMAGE
    team.felt = felt
    team.save()

    snap = _snapshot(client, team)

    assert snap["cardBack"]["style"] == "image"
    assert snap["cardBack"]["image"].endswith("blue.webp")
    assert snap["felt"]["style"] == "image"
    assert snap["felt"]["image"].endswith("wood.webp")


@pytest.mark.django_db
def test_colour_style_ignores_a_picked_image_but_still_carries_it(client, team, standard_deck, felt):
    """Switching back to a colour must not require picking the felt again."""
    team.felt = felt
    team.felt_style = SurfaceStyle.COLOR
    team.save()

    snap = _snapshot(client, team)

    assert snap["felt"]["style"] == "color"
    assert snap["felt"]["color"] == team.felt_color
    # The choice survives on the team even though the room renders the colour.
    team.refresh_from_db()
    assert team.felt_id == felt.pk


@pytest.mark.django_db
def test_styles_are_settable_through_the_api(client, team, standard_deck, felt):
    resp = client.patch(
        f"/api/v1/teams/{team.pk}/",
        {"felt_style": "image", "felt_id": felt.pk, "card_back_style": "color"},
        format="json",
    )

    assert resp.status_code == 200
    team.refresh_from_db()
    assert (team.felt_style, team.felt_id, team.card_back_style) == ("image", felt.pk, "color")


@pytest.mark.django_db
def test_an_unknown_style_is_rejected(client, team, standard_deck):
    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"felt_style": "gradient"}, format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_style"


@pytest.mark.django_db
def test_an_inactive_felt_cannot_be_picked(client, team, standard_deck):
    gone = Felt.objects.create(is_standard=False, image="decks/felts/gone.webp", name="Gone")
    Felt.objects.filter(pk=gone.pk).update(is_active=False)

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"felt_id": gone.pk}, format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "felt_unavailable"


@pytest.mark.django_db
def test_anonymous_room_gets_a_flat_felt_and_the_deck_back(client, standard_deck):
    resp = APIClient().post("/api/v1/rooms", {"title": "Retro", "username": "Alex"}, format="json")

    snap = resp.json()["deckSnapshot"]
    assert snap["felt"]["style"] == "color"
    assert snap["cardBack"]["style"] == "image"  # the deck's own back image
