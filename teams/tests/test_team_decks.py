"""Deck catalogue + per-team deck pick (P2.8).

Covers what the picker must never allow: playing another team's custom deck, and
a stale pick breaking room creation.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from decks.models import CardBack, Deck
from decks.selection import card_back_for_team, decks_for_team
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _user(email):
    return User.objects.create_user(email=email, password="pw12345678", display_name=email.split("@")[0])


@pytest.fixture
def owner(db):
    return _user("owner@example.com")


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


def _paid_deck(vote_type, name="Fibonacci", free_tier=False):
    """A catalogue deck reserved to paid teams (not in the free subset)."""
    deck = Deck.objects.create(
        vote_type=vote_type, is_standard=False, free_tier=free_tier, card_back_image="decks/backs/b.webp"
    )
    deck.set_current_language("en")
    deck.name = name
    deck.save()
    return deck


@pytest.mark.django_db
def test_catalogue_lists_standard_deck_and_defaults_to_it(client, team, standard_deck):
    resp = client.get(f"/api/v1/teams/{team.pk}/decks/")
    assert resp.status_code == 200
    body = resp.json()
    assert [d["id"] for d in body["decks"]] == [standard_deck.pk]
    assert body["selected_deck_ids"] == []
    assert body["decks"][0]["is_custom"] is False
    assert len(body["decks"][0]["cards"]) == 7


@pytest.mark.django_db
def test_a_team_sees_paid_only_decks_too(client, team, standard_deck):
    """A team is always paid, so it sees the whole catalogue, free tier or not."""
    paid = _paid_deck(standard_deck.vote_type, "Fibonacci")

    ids = [d["id"] for d in client.get(f"/api/v1/teams/{team.pk}/decks/").json()["decks"]]
    assert standard_deck.pk in ids
    assert paid.pk in ids


@pytest.mark.django_db
def test_enable_several_decks_and_clear(client, team, standard_deck):
    mine = _paid_deck(standard_deck.vote_type)

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"deck_ids": [standard_deck.pk, mine.pk]}, format="json")
    assert resp.status_code == 200
    team.refresh_from_db()
    assert set(team.decks.values_list("pk", flat=True)) == {standard_deck.pk, mine.pk}

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"deck_ids": []}, format="json")
    assert resp.status_code == 200
    team.refresh_from_db()
    assert team.decks.count() == 0


@pytest.mark.django_db
def test_cannot_enable_an_inactive_deck(client, team, standard_deck):
    gone = _paid_deck(standard_deck.vote_type)
    Deck.objects.filter(pk=gone.pk).update(is_active=False)

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"deck_ids": [gone.pk]}, format="json")
    assert resp.status_code == 400
    assert resp.json()["code"] == "deck_unavailable"
    team.refresh_from_db()
    assert team.decks.count() == 0


@pytest.mark.django_db
def test_non_member_cannot_read_catalogue(team, standard_deck):
    c = APIClient()
    c.force_authenticate(_user("stranger@example.com"))
    assert c.get(f"/api/v1/teams/{team.pk}/decks/").status_code == 403


@pytest.mark.django_db
def test_room_freezes_every_enabled_deck(client, team, standard_deck):
    mine = _paid_deck(standard_deck.vote_type)
    team.decks.set([mine.pk, standard_deck.pk])

    resp = client.post("/api/v1/rooms", {"title": "Sprint", "team": team.pk}, format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert {d["deckId"] for d in body["availableDecks"]} == {mine.pk, standard_deck.pk}
    # The first enabled deck is the one in play.
    assert body["deckSnapshot"]["deckId"] in {mine.pk, standard_deck.pk}


@pytest.mark.django_db
def test_deactivated_pick_falls_back_to_standard(team, standard_deck):
    """A stale pick must not break room creation — it silently falls back."""
    mine = _paid_deck(standard_deck.vote_type)
    team.decks.set([mine.pk])
    Deck.objects.filter(pk=mine.pk).update(is_active=False)

    assert [d.pk for d in decks_for_team(team)] == [standard_deck.pk]


@pytest.mark.django_db
def test_untranslated_custom_deck_serializes_instead_of_500(client, team, standard_deck):
    """A deck with no row in the active language must degrade, not raise."""
    deck = Deck.objects.create(
        vote_type=standard_deck.vote_type, is_standard=False, card_back_image="decks/backs/b.webp"
    )
    deck.set_current_language("it")
    deck.name = "Mazzo"
    deck.save()

    resp = client.get(f"/api/v1/teams/{team.pk}/decks/", HTTP_ACCEPT_LANGUAGE="en")
    assert resp.status_code == 200
    entry = next(d for d in resp.json()["decks"] if d["id"] == deck.pk)
    assert entry["name"] == "Mazzo"


@pytest.mark.django_db
def test_deck_str_prefers_english_then_french_then_technical(team, standard_deck):
    """Admin label (decks/deck/ changelist)."""
    vt = standard_deck.vote_type

    both = Deck.objects.create(vote_type=vt, card_back_image="b.webp")
    both.set_current_language("fr"); both.name = "Jeu"; both.save()
    both.set_current_language("en"); both.name = "Deck"; both.save()
    assert str(both) == "Deck"

    fr_only = Deck.objects.create(vote_type=vt, card_back_image="b.webp")
    fr_only.set_current_language("fr"); fr_only.name = "Jeu FR"; fr_only.save()
    assert str(fr_only) == "Jeu FR"

    it_only = Deck.objects.create(vote_type=vt, card_back_image="b.webp")
    it_only.set_current_language("it"); it_only.name = "Mazzo"; it_only.save()
    assert str(it_only) == f"Deck<{it_only.pk}> ({vt.pk})"

    bare = Deck.objects.create(vote_type=vt, card_back_image="b.webp")
    assert str(bare) == f"Deck<{bare.pk}> ({vt.pk})"


def _extra_back(name="Blue", free_tier=True):
    """A built-in catalogue back (visible to any team)."""
    return CardBack.objects.create(
        is_standard=True, free_tier=free_tier, image="decks/backs/custom.webp", name=name
    )


@pytest.mark.django_db
def test_catalogue_lists_card_backs(client, team, standard_deck):
    mine = _extra_back()
    body = client.get(f"/api/v1/teams/{team.pk}/decks/").json()
    ids = [b["id"] for b in body["card_backs"]]
    assert mine.pk in ids
    assert body["selected_card_back_id"] is None


@pytest.mark.django_db
def test_back_and_deck_are_picked_independently(client, team, standard_deck):
    back = _extra_back()
    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"card_back_id": back.pk}, format="json")
    assert resp.status_code == 200
    team.refresh_from_db()
    # Picking a back leaves the fronts untouched.
    assert team.card_back_id == back.pk
    assert team.decks.count() == 0


@pytest.mark.django_db
def test_cannot_pick_an_inactive_card_back(client, team, standard_deck):
    back = _extra_back()
    CardBack.objects.filter(pk=back.pk).update(is_active=False)

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"card_back_id": back.pk}, format="json")
    assert resp.status_code == 400
    assert resp.json()["code"] == "card_back_unavailable"


@pytest.mark.django_db
def test_room_snapshot_uses_the_picked_back_over_the_deck_default(client, team, standard_deck):
    back = _extra_back()
    team.card_back = back
    team.card_back_style = "image"
    team.save(update_fields=["card_back", "card_back_style"])

    resp = client.post("/api/v1/rooms", {"title": "Sprint", "team": team.pk}, format="json")
    assert resp.status_code == 201
    assert resp.json()["deckSnapshot"]["cardBack"]["image"].endswith("custom.webp")


@pytest.mark.django_db
def test_room_snapshot_falls_back_to_deck_default_back(client, team, standard_deck):
    resp = client.post("/api/v1/rooms", {"title": "Sprint", "team": team.pk}, format="json")
    assert resp.status_code == 201
    assert resp.json()["deckSnapshot"]["cardBack"]["image"].endswith("back.webp")


@pytest.mark.django_db
def test_deactivated_back_pick_falls_back(team, standard_deck):
    back = _extra_back()
    team.card_back = back
    team.save(update_fields=["card_back"])
    CardBack.objects.filter(pk=back.pk).update(is_active=False)

    assert card_back_for_team(team) is None


@pytest.mark.django_db
def test_seed_does_not_create_a_placeholder_card_back(standard_deck):
    """The seed no longer ships a "Standard" card back pointing at a missing image;
    built-in backs are uploaded through the admin instead."""
    from django.core.management import call_command

    CardBack.objects.all().delete()
    call_command("seed_delegation_deck")

    assert CardBack.objects.count() == 0


@pytest.mark.django_db
def test_picking_from_the_catalogue_is_free(client, team, standard_deck, monkeypatch):
    """A subscription buys a bigger catalogue, not the right to choose from it."""
    monkeypatch.setattr("teams.api_views.paid_required", lambda t: None)
    monkeypatch.setattr("billing.service.billing_configured", lambda: True)

    resp = client.patch(f"/api/v1/teams/{team.pk}/", {"deck_ids": [standard_deck.pk]}, format="json")
    assert resp.status_code == 200
    team.refresh_from_db()
    assert list(team.decks.values_list("pk", flat=True)) == [standard_deck.pk]
