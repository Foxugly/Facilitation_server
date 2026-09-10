"""Read-only exposure of the deck referential, so a team can pick which deck its
rooms are dealt from. Rooms never read these serializers — they freeze an immutable
``deck_snapshot`` at creation (spec §4); this is the *catalogue*, not the game state.
"""
from rest_framework import serializers

from config.media import absolute_media_url as _media_url

from .models import Background, Card, CardBack, Deck, Felt


class DeckCardPreviewSerializer(serializers.ModelSerializer):
    """Just enough to render a deck thumbnail — not the full card definition."""

    image = serializers.SerializerMethodField()

    class Meta:
        model = Card
        fields = ["value", "slug", "order", "image"]

    def get_image(self, card) -> str:
        return _media_url(card.background_image)


def _translated(obj, field: str) -> str:
    """Read a parler field without exploding on a missing translation.

    Plain attribute access raises DoesNotExist (→ 500) when neither the active
    language nor the fallback has a row — a real possibility for a custom deck
    that isn't translated into all five languages.
    """
    return obj.safe_translation_getter(field, any_language=True) or ""


class DeckSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    vote_type_code = serializers.CharField(source="vote_type.code", read_only=True)
    vote_type_name = serializers.SerializerMethodField()
    card_back_image = serializers.SerializerMethodField()
    is_custom = serializers.SerializerMethodField()
    cards = serializers.SerializerMethodField()

    class Meta:
        model = Deck
        fields = [
            "id", "name", "vote_type_code", "vote_type_name", "is_standard",
            "is_custom", "card_back_image", "cards",
        ]

    def get_cards(self, deck) -> list:
        cards = deck.cards.filter(is_active=True).order_by("order")
        return DeckCardPreviewSerializer(cards, many=True).data

    def get_name(self, deck) -> str:
        return _translated(deck, "name")

    def get_vote_type_name(self, deck) -> str:
        return _translated(deck.vote_type, "name")

    def get_card_back_image(self, deck) -> str:
        return _media_url(deck.card_back_image)

    def get_is_custom(self, deck) -> bool:
        # No per-entry ownership yet; returns True once user uploads land.
        return False


class CardBackSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    is_custom = serializers.SerializerMethodField()

    class Meta:
        model = CardBack
        fields = ["id", "name", "is_standard", "is_custom", "image"]

    def get_image(self, back) -> str:
        return _media_url(back.image)

    def get_is_custom(self, back) -> bool:
        return back.uploaded_by_id is not None


class FeltSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    is_custom = serializers.SerializerMethodField()

    class Meta:
        model = Felt
        fields = ["id", "name", "is_standard", "is_custom", "image"]

    def get_image(self, felt) -> str:
        return _media_url(felt.image)

    def get_is_custom(self, felt) -> bool:
        return felt.uploaded_by_id is not None


class BackgroundSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    is_custom = serializers.SerializerMethodField()

    class Meta:
        model = Background
        fields = ["id", "name", "is_standard", "is_custom", "image"]

    def get_image(self, background) -> str:
        return _media_url(background.image)

    def get_is_custom(self, background) -> bool:
        return background.uploaded_by_id is not None
