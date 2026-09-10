"""Le fond de la salle : la page derriere la table.

Contrairement au feutre et au dos, il a un troisieme etat, THEME, qui est le
defaut et ou l'equipe n'impose rien — la page garde le mode clair/sombre du
visiteur. C'est ce qui permet a une equipe existante de ne rien voir changer.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from decks.models import Background
from teams.models import BackgroundStyle, Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="bg@example.com", password="pw12345678", display_name="O")


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
def background(db, owner):
    return Background.objects.create(
        is_standard=False, free_tier=False, uploaded_by=owner, image="decks/backgrounds/loft.webp", name="Loft"
    )


def _snapshot(client, team):
    resp = client.post("/api/v1/rooms", {"title": "Retro", "team": team.pk}, format="json")
    assert resp.status_code == 201
    return resp.json()["deckSnapshot"]


@pytest.mark.django_db
def test_default_imposes_nothing(client, team, standard_deck):
    """Le defaut ne doit peindre ni couleur ni image : la salle suit le theme."""
    snap = _snapshot(client, team)

    assert snap["background"]["style"] == "theme"
    assert snap["background"]["image"] is None


@pytest.mark.django_db
def test_image_style_uses_the_picked_background(client, team, standard_deck, background):
    team.background_style = BackgroundStyle.IMAGE
    team.background = background
    team.save()

    snap = _snapshot(client, team)

    assert snap["background"]["style"] == "image"
    assert snap["background"]["image"].endswith("loft.webp")


@pytest.mark.django_db
def test_image_style_without_a_pick_falls_back_to_the_colour(client, team, standard_deck):
    """Une image choisie puis desactivee ne doit pas laisser la salle sans fond."""
    team.background_style = BackgroundStyle.IMAGE
    team.save()

    snap = _snapshot(client, team)

    assert snap["background"]["style"] == "color"
    assert snap["background"]["color"] == team.background_color


@pytest.mark.django_db
def test_colour_style_still_carries_the_picked_image(client, team, standard_deck, background):
    team.background = background
    team.background_style = BackgroundStyle.COLOR
    team.save()

    snap = _snapshot(client, team)

    assert snap["background"]["style"] == "color"
    team.refresh_from_db()
    assert team.background_id == background.pk


@pytest.mark.django_db
def test_style_and_pick_are_settable_through_the_api(client, team, standard_deck, background):
    resp = client.patch(
        f"/api/v1/teams/{team.pk}/",
        {"background_style": "image", "background_id": background.pk},
        format="json",
    )

    assert resp.status_code == 200
    team.refresh_from_db()
    assert (team.background_style, team.background_id) == ("image", background.pk)


@pytest.mark.django_db
def test_null_resets_the_pick(client, team, standard_deck, background):
    team.background = background
    team.save()

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"background_id": None}, format="json")

    assert resp.status_code == 200
    team.refresh_from_db()
    assert team.background_id is None


@pytest.mark.django_db
def test_an_unknown_style_is_rejected(client, team, standard_deck):
    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"background_style": "gradient"}, format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_style"


@pytest.mark.django_db
def test_another_squads_upload_cannot_be_picked(client, team, standard_deck, db):
    """Le catalogue de fonds n'a que des televersements : l'isolation entre squads
    est donc la seule chose qui protege ce choix."""
    stranger = User.objects.create_user(email="x@example.com", password="pw12345678", display_name="X")
    theirs = Background.objects.create(
        is_standard=False, free_tier=False, uploaded_by=stranger, image="decks/backgrounds/theirs.webp"
    )

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"background_id": theirs.pk}, format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "background_unavailable"


@pytest.mark.django_db
def test_catalogue_is_served_with_the_team_decks(client, team, standard_deck, background):
    resp = client.get(f"/api/v1/teams/{team.pk}/decks/")

    assert resp.status_code == 200
    body = resp.json()
    assert [b["id"] for b in body["backgrounds"]] == [background.pk]
    assert body["selected_background_id"] is None
