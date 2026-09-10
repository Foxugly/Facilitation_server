"""The admin 'duplicate card' action clones a card within its deck, layers included."""
import pytest

from decks.admin import duplicate_card
from decks.seed import create_standard_deck
from decks.models import Card, TextLayer


@pytest.mark.django_db
def test_duplicate_card_clones_card_with_unique_value_and_order():
    deck = create_standard_deck()
    original = deck.cards.get(value="4")
    before = deck.cards.count()

    clone = duplicate_card(original)

    assert deck.cards.count() == before + 1
    assert clone.pk != original.pk
    assert clone.deck_id == deck.id
    # Unique-constraint fields must differ within the deck.
    assert clone.value != original.value
    assert clone.order == before + 1  # appended at the end
    assert not deck.cards.exclude(pk=clone.pk).filter(value=clone.value).exists()
    # Same image reference (no re-upload), same active flag.
    assert clone.background_image.name == original.background_image.name


@pytest.mark.django_db
def test_duplicate_card_copies_layers_and_all_translations():
    deck = create_standard_deck()
    original = deck.cards.get(value="4")
    orig_layers = list(original.layers.all())

    clone = duplicate_card(original)

    assert clone.layers.count() == len(orig_layers)
    # The i18n name layer carries its per-language rows across.
    src_i18n = original.layers.order_by("order").last()
    dst_i18n = clone.layers.order_by("order").last()
    src_langs = {t.language_code: t.content for t in src_i18n.translations.all()}
    dst_langs = {t.language_code: t.content for t in dst_i18n.translations.all()}
    assert dst_langs == src_langs
    assert dst_langs.get("fr") == "S'accorder"


@pytest.mark.django_db
def test_duplicating_twice_keeps_values_and_orders_unique():
    deck = create_standard_deck()
    original = deck.cards.get(value="4")

    first = duplicate_card(original)
    second = duplicate_card(original)

    assert first.value != second.value
    assert first.order != second.order
    # No unique-constraint collisions anywhere in the deck.
    values = list(deck.cards.values_list("value", flat=True))
    orders = list(deck.cards.values_list("order", flat=True))
    assert len(values) == len(set(values))
    assert len(orders) == len(set(orders))


@pytest.mark.django_db
def test_admin_action_wired_on_changelist():
    """End-to-end through the admin action machinery (not just the helper)."""
    from django.contrib.auth import get_user_model
    from django.test import Client

    User = get_user_model()
    admin = User.objects.create_superuser(email="a@ex.com", password="pw12345678", display_name="A")
    deck = create_standard_deck()
    card = deck.cards.get(value="4")
    before = deck.cards.count()

    client = Client()
    client.force_login(admin)
    resp = client.post(
        "/admin/decks/card/",
        {"action": "duplicate_selected_cards", "_selected_action": [str(card.pk)]},
        follow=True,
    )

    assert resp.status_code == 200
    assert deck.cards.count() == before + 1
