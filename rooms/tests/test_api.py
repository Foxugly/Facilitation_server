import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from rooms.models import Participant, Role, Room


@pytest.mark.django_db
def test_a_room_is_capped_at_fifteen_participants(client, standard_deck):
    """Quinze et non vingt : au-dela, les cartes deviennent trop petites autour de la
    table pour rester lisibles — chaque joueur supplementaire retrecit toutes les
    cartes, mesure a 1536x864 : 124px a 5 joueurs, 85 a 12, 61 a 15, 46 a 20.

    Verrouille ici plutot que laisse au reglage : la valeur est un choix produit,
    repris tel quel dans le message « salle pleine » et sur la page des tarifs.
    """
    from rooms.models import Room

    code = client.post("/api/v1/rooms", {"username": "Sam"}, format="json").json()["code"]

    assert Room.objects.get(code=code).max_participants == 15


@override_settings(ROOM_MAX_PARTICIPANTS=2)
@pytest.mark.django_db
def test_join_rejected_when_room_full(client, standard_deck):
    # Creator counts as the first seat; cap is 2.
    code = client.post("/api/v1/rooms", {"username": "Sam"}, format="json").json()["code"]
    assert client.post(f"/api/v1/rooms/{code}/join", {"username": "Alex"}, format="json").status_code == 200
    resp = client.post(f"/api/v1/rooms/{code}/join", {"username": "Max"}, format="json")
    assert resp.status_code == 403 and resp.json()["code"] == "room_full"


@pytest.fixture
def client():
    return APIClient()


@pytest.mark.django_db
def test_create_room_returns_token_and_snapshot(client, standard_deck):
    resp = client.post("/api/v1/rooms", {"title": "Retro", "username": "Sam"}, format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["code"]) == 6
    assert body["role"] == Role.FACILITATOR
    assert body["participantToken"]
    assert body["deckSnapshot"]["voteType"] == "delegation_poker"
    assert len(body["deckSnapshot"]["cards"]) == 7

    room = Room.objects.get(code=body["code"])
    assert room.title == "Retro"
    fac = Participant.objects.get(token=body["participantToken"])
    assert fac.role == Role.FACILITATOR and fac.display_name == "Sam"


@pytest.mark.django_db
def test_create_room_without_deck_is_503(client):
    resp = client.post("/api/v1/rooms", {"username": "Sam"}, format="json")
    assert resp.status_code == 503


@pytest.mark.django_db
def test_join_room_case_insensitive(client, standard_deck):
    code = client.post("/api/v1/rooms", {"username": "Sam"}, format="json").json()["code"]
    resp = client.post(f"/api/v1/rooms/{code.lower()}/join", {"username": "Alex"}, format="json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == Role.VOTER
    assert body["participantToken"]
    assert len(body["deckSnapshot"]["cards"]) == 7


@pytest.mark.django_db
def test_join_unknown_room_404(client, standard_deck):
    resp = client.post("/api/v1/rooms/ZZZZZZ/join", {"username": "Alex"}, format="json")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_room_exists_endpoint(client, standard_deck):
    code = client.post("/api/v1/rooms", {"username": "Sam", "title": "Retro"}, format="json").json()["code"]
    assert client.get(f"/api/v1/rooms/{code}").json() == {"code": code, "roomTitle": "Retro", "exists": True, "isTeam": False}
    assert client.get("/api/v1/rooms/ZZZZZZ").json()["exists"] is False


@pytest.mark.django_db
def test_snapshot_layer_text_static_vs_i18n(client, standard_deck):
    body = client.post("/api/v1/rooms", {"username": "Sam"}, format="json").json()
    card = body["deckSnapshot"]["cards"][0]
    layers = {layer["order"]: layer for layer in card["layers"]}
    assert layers[1]["kind"] == "static" and layers[1]["text"] == "1"
    assert layers[2]["kind"] == "i18n"
    assert layers[2]["text"]["en"] == "Tell" and layers[2]["text"]["fr"] == "Dire"
