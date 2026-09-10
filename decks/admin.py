from django.contrib import admin
from django.db.models import Max
from parler.admin import TranslatableAdmin, TranslatableTabularInline

from .models import Background, Card, CardBack, Deck, Felt, TextLayer, VoteType


def duplicate_card(card):
    """Clone one Card within its deck, with its text layers (all translations).

    The copy is appended at the end of the deck's order and gets a distinct value/slug
    to satisfy the (deck, value) / (deck, order) unique constraints. The image file is
    referenced, not re-uploaded — the duplicate points at the same stored image."""
    deck = card.deck
    next_order = (deck.cards.aggregate(m=Max("order"))["m"] or 0) + 1
    base_value = f"{(card.value or '')[:26]}-copy"
    new_value, i = base_value, 2
    while deck.cards.filter(value=new_value).exists():
        new_value = f"{base_value}-{i}"[:32]
        i += 1
    clone = Card.objects.create(
        deck=deck,
        value=new_value,
        slug=f"{card.slug}-copy"[:50],
        order=next_order,
        background_image=card.background_image.name,
        is_active=card.is_active,
    )
    for layer in card.layers.all():
        new_layer = TextLayer.objects.create(
            card=clone,
            order=layer.order,
            pos_x=layer.pos_x,
            pos_y=layer.pos_y,
            font_family=layer.font_family,
            font_size=layer.font_size,
            font_weight=layer.font_weight,
            color=layer.color,
            align=layer.align,
            content_kind=layer.content_kind,
            # Reference l'image, sans la re-televerser — comme background_image
            # ci-dessus. L'oublier ferait perdre son pictogramme au double.
            image=layer.image.name,
        )
        for tr in layer.translations.all():
            new_layer.translations.create(language_code=tr.language_code, content=tr.content)
    return clone


@admin.register(VoteType)
class VoteTypeAdmin(TranslatableAdmin):
    list_display = ("code", "resolution_strategy", "is_active")


class TextLayerInline(TranslatableTabularInline):
    model = TextLayer
    extra = 0


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("slug", "value", "order", "deck", "is_active")
    list_filter = ("deck",)
    ordering = ("deck", "order")
    inlines = [TextLayerInline]
    actions = ["duplicate_selected_cards"]

    @admin.action(description="Dupliquer les cartes sélectionnées")
    def duplicate_selected_cards(self, request, queryset):
        count = 0
        for card in queryset.select_related("deck").prefetch_related("layers__translations"):
            duplicate_card(card)
            count += 1
        self.message_user(request, f"{count} carte(s) dupliquée(s).")


@admin.register(Deck)
class DeckAdmin(TranslatableAdmin):
    list_display = ("__str__", "vote_type", "is_standard", "free_tier", "is_active")
    list_filter = ("is_standard", "free_tier", "is_active")


@admin.register(CardBack)
class CardBackAdmin(admin.ModelAdmin):
    list_display = ("__str__", "is_standard", "free_tier", "uploaded_by", "is_active")
    list_filter = ("is_standard", "free_tier", "is_active")


@admin.register(Felt)
class FeltAdmin(admin.ModelAdmin):
    list_display = ("__str__", "is_standard", "free_tier", "uploaded_by", "is_active")
    list_filter = ("is_standard", "free_tier", "is_active")


# Rien n'est livre dans ce catalogue : l'admin est le seul endroit d'ou promouvoir
# un televersement en fond built-in, si l'envie vient plus tard.
@admin.register(Background)
class BackgroundAdmin(admin.ModelAdmin):
    list_display = ("__str__", "is_standard", "free_tier", "uploaded_by", "is_active")
    list_filter = ("is_standard", "free_tier", "is_active")
