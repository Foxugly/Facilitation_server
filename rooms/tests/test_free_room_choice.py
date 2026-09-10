"""Salle sans compte : aucun choix a la creation.

Le type de poker se choisit **en salle** (le facilitateur en change round par round,
la salle portant tout le catalogue gratuit), et le dos des cartes est **impose**. La
page d'accueil ne demande donc plus que le titre et le nom.

Le corollaire qui compte : plus rien de ce que porte la charge utile ne decide du
contenu de la salle, donc plus rien a filtrer — un deck payant ne peut pas etre
glisse dans la creation, faute de champ pour le porter.
"""
import pytest
from rest_framework.test import APIClient

from decks.models import CardBack, Deck


def _extra_deck(vote_type, *, free_tier, name="Extra"):
    """Un deck commun (possede par personne) dans l'offre gratuite ou non."""
    deck = Deck.objects.create(
        vote_type=vote_type, is_standard=True, free_tier=free_tier, card_back_image="decks/backs/b.webp"
    )
    deck.set_current_language("en")
    deck.name = name
    deck.save()
    deck.cards.create(value="13", slug="thirteen", order=1, background_image="decks/cards/13.webp")
    return deck


@pytest.fixture
def client():
    return APIClient()


@pytest.mark.django_db
def test_only_the_free_subset_is_offered_to_an_account_less_room(standard_deck):
    """La distinction qui compte est ``free_tier``, non ``is_standard`` : le premier
    dit qui a droit au deck, le second d'ou il vient."""
    from decks.selection import free_decks

    paid_only = _extra_deck(standard_deck.vote_type, free_tier=False, name="Paid only")
    free_extra = _extra_deck(standard_deck.vote_type, free_tier=True, name="Free extra")

    offerts = {d.pk for d in free_decks()}

    assert free_extra.pk in offerts
    assert paid_only.pk not in offerts


@pytest.mark.django_db
def test_room_carries_the_whole_free_catalogue_and_starts_on_the_first(client, standard_deck):
    """Le facilitateur change de type en salle : tout le catalogue gratuit est fige
    dans la salle, et le premier fait office de type de depart."""
    extra = _extra_deck(standard_deck.vote_type, free_tier=True)

    resp = client.post("/api/v1/rooms", {"title": "Retro", "username": "Alex"}, format="json")

    assert resp.status_code == 201
    body = resp.json()
    assert {d["deckId"] for d in body["availableDecks"]} == {standard_deck.pk, extra.pk}
    assert body["deckSnapshot"]["deckId"] == standard_deck.pk


@pytest.mark.django_db
def test_a_deck_sent_in_the_payload_is_simply_ignored(client, standard_deck):
    """Un client d'une version anterieure envoie encore deck_ids : sans effet, et
    surtout sans permettre de faire entrer un deck payant."""
    paid_only = _extra_deck(standard_deck.vote_type, free_tier=False)

    resp = client.post(
        "/api/v1/rooms",
        {"title": "Retro", "username": "Alex", "deck_ids": [paid_only.pk]},
        format="json",
    )

    assert resp.status_code == 201
    assert {d["deckId"] for d in resp.json()["availableDecks"]} == {standard_deck.pk}


@pytest.mark.django_db
def test_the_card_back_is_imposed_from_the_catalogue(client, standard_deck):
    """Le dos vient du catalogue, jamais du deck.

    Le dos propre au deck standard pointe sur « decks/backs/back.webp », un
    placeholder dont le fichier n'a jamais ete televerse : y retomber affichait une
    carte face cachee entierement nue.
    """
    impose = CardBack.objects.create(
        is_standard=True, free_tier=True, image="decks/backs/impose.webp", name="Impose"
    )

    resp = client.post("/api/v1/rooms", {"title": "Retro", "username": "Alex"}, format="json")

    assert resp.status_code == 201
    assert resp.json()["deckSnapshot"]["cardBack"]["image"].endswith("impose.webp")
    assert impose.pk  # le dos vient bien du catalogue


@pytest.mark.django_db
def test_a_card_back_sent_in_the_payload_is_simply_ignored(client, standard_deck):
    CardBack.objects.create(
        is_standard=True, free_tier=True, image="decks/backs/impose.webp", name="Impose"
    )
    autre = CardBack.objects.create(
        is_standard=True, free_tier=True, image="decks/backs/autre.webp", name="Autre"
    )

    resp = client.post(
        "/api/v1/rooms", {"title": "Retro", "username": "Alex", "card_back_id": autre.pk}, format="json"
    )

    assert resp.status_code == 201
    assert resp.json()["deckSnapshot"]["cardBack"]["image"].endswith("impose.webp")


@pytest.mark.django_db
def test_a_paid_only_card_back_is_never_imposed(client, standard_deck):
    CardBack.objects.create(
        is_standard=True, free_tier=False, image="decks/backs/paid.webp", name="Paid"
    )
    gratuit = CardBack.objects.create(
        is_standard=True, free_tier=True, image="decks/backs/gratuit.webp", name="Gratuit"
    )

    resp = client.post("/api/v1/rooms", {"title": "Retro", "username": "Alex"}, format="json")

    assert resp.status_code == 201
    assert resp.json()["deckSnapshot"]["cardBack"]["image"].endswith("gratuit.webp")
    assert gratuit.pk


@pytest.mark.django_db
def test_an_empty_catalogue_falls_back_to_the_deck_own_back(client, standard_deck):
    """Aucun dos au catalogue : on retombe sur celui du deck plutot que de refuser
    la creation — une salle sans dos vaut mieux que pas de salle."""
    assert not CardBack.objects.exists()

    resp = client.post("/api/v1/rooms", {"title": "Retro", "username": "Alex"}, format="json")

    assert resp.status_code == 201
    assert resp.json()["deckSnapshot"]["cardBack"]["image"].endswith("back.webp")


@pytest.mark.django_db
def test_a_free_tier_custom_back_can_be_imposed(client, standard_deck):
    """Meme regle pilotable en admin que pour les decks : free_tier + actif suffit —
    un dos custom (non standard) peut etre promu dans l'offre gratuite, et donc
    servir de dos impose. ``is_standard`` n'est pas requis ici."""
    CardBack.objects.create(
        is_standard=False, free_tier=True, image="decks/backs/custom.webp", name="Custom free"
    )

    resp = client.post("/api/v1/rooms", {"title": "Retro", "username": "Alex"}, format="json")

    assert resp.status_code == 201
    assert resp.json()["deckSnapshot"]["cardBack"]["image"].endswith("custom.webp")
