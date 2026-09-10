"""Mise en page du depouillement : le reglage d'equipe, et son figeage sur la salle.

Une fois les votes reveles la main ne sert plus a rien, et le depouillement prend sa
place. Deux formes possibles — les cartes jouees, ou des lignes chiffrees — reglees
par equipe. Les cartes sont le defaut : c'est la seule forme lisible sur un deck a
pictogrammes, dont les cartes n'ont aucun libelle.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from decks.seed import create_standard_deck
from realtime.services import build_state_sync
from rooms.models import Room
from teams.models import ResultLayout, Team, TeamMembership, TeamRole

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
def standard_deck(db):
    return create_standard_deck()


def test_a_team_shows_the_played_cards_by_default(team):
    assert team.result_layout == ResultLayout.CARDS


def test_a_manager_switches_to_the_tallied_summary(client, team):
    resp = client.patch(f"/api/v1/teams/{team.id}/", {"result_layout": "summary"}, format="json")

    assert resp.status_code == 200
    assert resp.json()["result_layout"] == "summary"
    team.refresh_from_db()
    assert team.result_layout == ResultLayout.SUMMARY


def test_an_unknown_layout_is_rejected(client, team):
    resp = client.patch(f"/api/v1/teams/{team.id}/", {"result_layout": "carousel"}, format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_result_layout"
    team.refresh_from_db()
    assert team.result_layout == ResultLayout.CARDS


def test_a_room_freezes_the_layout_its_team_had_at_creation(client, team, standard_deck):
    """Sans ce figeage, basculer le reglage changerait la mise en page d'une partie en
    cours sous les yeux des joueurs."""
    team.result_layout = ResultLayout.SUMMARY
    team.save(update_fields=["result_layout"])

    code = client.post("/api/v1/rooms", {"username": "Sam", "team": team.id}, format="json").json()["code"]
    team.result_layout = ResultLayout.CARDS
    team.save(update_fields=["result_layout"])

    room = Room.objects.get(code=code)
    assert room.result_layout == ResultLayout.SUMMARY
    # Le client ne lit que l'etat diffuse : sans cette clef, la salle garderait sa mise
    # en page en base sans que personne ne la voie.
    assert build_state_sync(room.participants.first())["resultLayout"] == ResultLayout.SUMMARY


def test_an_anonymous_room_shows_the_played_cards(standard_deck):
    """Une salle sans equipe n'a personne pour regler quoi que ce soit."""
    code = APIClient().post("/api/v1/rooms", {"username": "Sam"}, format="json").json()["code"]

    assert Room.objects.get(code=code).result_layout == ResultLayout.CARDS
