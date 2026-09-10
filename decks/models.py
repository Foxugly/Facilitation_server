"""Editable deck referential (data-model spec §3). Translations live as parler rows
keyed by language_code ("langue = donnée", scope §10) — never label_fr columns.

At room creation the referential is frozen into an immutable JSON ``deck_snapshot``
(spec §4); the realtime layer never reads these tables.
"""
from django.conf import settings
from django.db import models
from parler.models import TranslatableModel, TranslatedFields


class TextLayerKind(models.TextChoices):
    STATIC = "static", "Static (one value, all languages)"
    I18N = "i18n", "Translated (per-language)"
    ICON = "icon", "Icon (draws the layer's uploaded image)"


class LayerAlign(models.TextChoices):
    LEFT = "left", "Left"
    CENTER = "center", "Center"
    RIGHT = "right", "Right"


class VoteType(TranslatableModel):
    """Abstract vote type (spec §3.1). Phase 1: a single ``delegation_poker`` row.
    ``resolution_strategy`` is an identifier routed in code (principle P1)."""

    code = models.SlugField(unique=True)
    resolution_strategy = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    translations = TranslatedFields(
        name=models.CharField(max_length=120),
    )

    def __str__(self):
        return self.code


class CardBack(models.Model):
    """A card back, catalogued independently of the fronts.

    A team picks its fronts (``Deck``) and its back separately, so any back can be
    paired with any deck. ``Deck.card_back_image`` stays as the deck's own default,
    used when a team hasn't picked a back. A built-in back is visible per ``free_tier``;
    a custom (uploaded) back is visible to its uploader's squad (see decks.selection).

    ``name`` is a plain field, NOT a parler one: it is a catalogue label ("Standard",
    "Blue"), not prose to translate — unlike deck names and card text layers.
    """

    # is_standard is the *built-in vs custom* discriminator: True = shipped
    # catalogue, False = a squad member's upload. NOT uploaded_by-is-null, so that
    # an orphaned upload (uploader deleted → uploaded_by null) stays custom/hidden
    # rather than turning into a global built-in.
    is_standard = models.BooleanField(default=True)
    # Included in the free offer; reserved to paid teams when false.
    free_tier = models.BooleanField(default=True)
    # The user who uploaded a custom entry (null for built-ins). Visibility flows
    # through the uploader's "squad" — see decks.selection.
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name = models.CharField(max_length=120, blank=True, default="")
    image = models.ImageField(upload_to="decks/backs/")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or f"CardBack<{self.pk}>"


class Felt(models.Model):
    """A table felt, catalogued like card backs so a team can pick an image instead
    of a flat colour. Visibility is by ``free_tier``; ``name`` is a plain label."""

    is_standard = models.BooleanField(default=True)  # built-in vs upload — see CardBack
    free_tier = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name = models.CharField(max_length=120, blank=True, default="")
    image = models.ImageField(upload_to="decks/felts/")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or f"Felt<{self.pk}>"


class Background(models.Model):
    """The page behind the table, catalogued like felts and card backs.

    Nothing ships in this catalogue: unlike felts and backs, it fills up only with
    squad uploads (a full-bleed backdrop is a house choice, not something a generic
    built-in does well). ``is_standard`` / ``free_tier`` are kept all the same, so
    built-ins can be added later from the admin without a migration.
    """

    is_standard = models.BooleanField(default=True)  # built-in vs upload — see CardBack
    free_tier = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name = models.CharField(max_length=120, blank=True, default="")
    image = models.ImageField(upload_to="decks/backgrounds/")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or f"Background<{self.pk}>"


class Deck(TranslatableModel):
    """A set of cards for a vote type (spec §3.2). Every deck is a shared catalogue
    entry; ``free_tier`` decides whether an account-less room may play it."""

    vote_type = models.ForeignKey(VoteType, on_delete=models.PROTECT, related_name="decks")
    is_standard = models.BooleanField(default=True)
    # Included in the free offer; reserved to paid teams when false.
    free_tier = models.BooleanField(default=True)
    card_back_image = models.ImageField(upload_to="decks/backs/")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    translations = TranslatedFields(
        name=models.CharField(max_length=120),
    )

    def __str__(self):
        # Admin label: the English name, else the French one, else a technical
        # fallback — a deck with no translation at all must still be listable.
        for lang in ("en", "fr"):
            name = self.safe_translation_getter("name", language_code=lang, any_language=False)
            if name:
                return name
        return f"Deck<{self.pk}> ({self.vote_type_id})"


class Card(models.Model):
    """A card of a deck (spec §3.3). ``value`` is the language-agnostic canonical
    value referenced by Vote.card_value / Result.chosen_value (Delegation Poker: "1".."7")."""

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="cards")
    value = models.CharField(max_length=32)
    slug = models.SlugField()
    order = models.PositiveSmallIntegerField()
    background_image = models.ImageField(upload_to="decks/cards/")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("deck", "order")
        constraints = [
            models.UniqueConstraint(fields=("deck", "value"), name="uniq_card_deck_value"),
            models.UniqueConstraint(fields=("deck", "order"), name="uniq_card_deck_order"),
        ]

    def __str__(self):
        return f"{self.slug} ({self.value})"


class TextLayer(TranslatableModel):
    """N overlaid layers per card (spec §3.4). Position/size in % (responsive);
    ``content`` is a translated field — a ``static`` layer stores only the fallback
    (EN) row and parler serves it everywhere; an ``i18n`` layer stores N rows.

    An ``icon`` layer draws ``image`` instead of text. The image is data like every
    other visual of this app (card fronts, backs, felts): adding a pictogram is an
    upload, never a frontend release. The client paints it as a mask filled with
    ``color``, so a pictogram follows a team's palette instead of carrying its own.
    """

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="layers")
    order = models.PositiveSmallIntegerField(default=1)
    pos_x = models.DecimalField(max_digits=5, decimal_places=2)  # % 0-100
    pos_y = models.DecimalField(max_digits=5, decimal_places=2)  # % 0-100
    font_family = models.CharField(max_length=80, default="Inter")
    font_size = models.DecimalField(max_digits=5, decimal_places=2)  # % of card height
    font_weight = models.PositiveSmallIntegerField(default=400)
    color = models.CharField(max_length=9, default="#ffffff")  # #RRGGBB[AA]
    align = models.CharField(max_length=6, choices=LayerAlign.choices, default=LayerAlign.CENTER)
    content_kind = models.CharField(max_length=6, choices=TextLayerKind.choices, default=TextLayerKind.I18N)
    # Le pictogramme d'une couche ``icon``. Vide sur une couche de texte.
    image = models.ImageField(upload_to="decks/icons/", blank=True)

    translations = TranslatedFields(
        # Vide sur une couche ``icon`` : celle-ci dessine son ``image``, et exiger
        # un texte qui ne sera jamais rendu ne ferait qu'egarer l'admin.
        content=models.CharField(max_length=200, blank=True),
    )

    class Meta:
        ordering = ("card", "order")

    def __str__(self):
        return f"Layer<{self.pk}> card={self.card_id} order={self.order}"
