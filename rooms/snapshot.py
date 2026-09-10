"""Deck snapshot builder (data-model spec §4).

Serializes the editable ``decks`` referential into an immutable, self-contained JSON
blob frozen on the room at creation. The realtime layer reads only this blob, so an
admin editing a deck later never mutates a live room (scope §3.6 immutability).
"""
from django.conf import settings

from decks.models import Deck, TextLayerKind


def _media_url(image_field):
    if not image_field:
        return None
    url = image_field.url
    if url.startswith("http"):
        return url
    return f"{settings.PUBLIC_MEDIA_BASE_URL.rstrip('/')}{url}"


def _layer_text(layer):
    """Static/icon → single string (fallback/EN row); i18n → {lang: text} across LANGUAGES.

    Une couche ``icon`` porte une cle d'icone resolue par le client : ce n'est pas de la
    prose, elle ne se traduit pas et sort donc en chaine simple comme une couche statique.
    """
    if layer.content_kind in (TextLayerKind.STATIC, TextLayerKind.ICON):
        return layer.safe_translation_getter("content", any_language=True) or ""
    text = {}
    for code, _ in settings.LANGUAGES:
        value = layer.safe_translation_getter("content", language_code=code, any_language=False)
        if value:
            text[code] = value
    return text


def build_deck_snapshot(deck: Deck, card_back=None) -> dict:
    """``card_back`` overrides the deck's own back image (a team picks the two
    independently); None keeps the deck's default. A team's chosen styles are
    applied afterwards by ``apply_team_appearance``."""
    vote_type = deck.vote_type
    cards = []
    for card in deck.cards.filter(is_active=True).prefetch_related("layers__translations").order_by("order"):
        layers = []
        for layer in card.layers.all().order_by("order"):
            layers.append(
                {
                    "kind": layer.content_kind,
                    "order": layer.order,
                    "x": float(layer.pos_x),
                    "y": float(layer.pos_y),
                    "font": layer.font_family,
                    "size": float(layer.font_size),
                    "weight": layer.font_weight,
                    "color": layer.color,
                    "align": layer.align,
                    "text": _layer_text(layer),
                    # Le pictogramme d'une couche ``icon`` — None sur une couche de texte.
                    # Le client le peint en masque teinte par ``color``, jamais en <img>.
                    "icon": _media_url(layer.image),
                }
            )
        cards.append(
            {
                "value": card.value,
                "slug": card.slug,
                "order": card.order,
                "background": {"image": _media_url(card.background_image)},
                "layers": layers,
            }
        )
    return {
        "voteType": vote_type.code,
        "resolutionStrategy": vote_type.resolution_strategy,
        "deckId": deck.pk,
        # Each surface carries its style so the client never has to guess whether a
        # colour or an image wins. Anonymous rooms: the deck's own back, flat felt.
        "cardBack": {
            "style": "image",
            "image": _media_url(card_back.image if card_back is not None else deck.card_back_image),
            "color": DEFAULT_CARD_BACK_COLOR,
        },
        "felt": {"style": "color", "image": None, "color": DEFAULT_FELT_COLOR},
        # Kept for older clients reading theme.* — remove once none are left.
        "theme": {"cardBackColor": DEFAULT_CARD_BACK_COLOR, "feltColor": DEFAULT_FELT_COLOR},
        "cards": cards,
    }


# Defaults mirror the frontend's built-in room look (dark card-back base, emerald felt).
DEFAULT_CARD_BACK_COLOR = "#143d2f"
DEFAULT_FELT_COLOR = "#10b981"


def apply_team_appearance(snapshot: dict, team, card_back=None, felt=None, background=None) -> dict:
    """Stamp a team's appearance onto a room snapshot, honouring its styles.

    The style decides which of the two representations the client renders; the
    other is still carried so switching back needs no new snapshot.
    """
    from teams.models import BackgroundStyle, SurfaceStyle

    back_is_image = team.card_back_style == SurfaceStyle.IMAGE
    snapshot["cardBack"] = {
        "style": "image" if back_is_image else "color",
        "image": _media_url(card_back.image) if (back_is_image and card_back is not None)
        else snapshot.get("cardBack", {}).get("image"),
        "color": team.card_back_color,
    }
    felt_is_image = team.felt_style == SurfaceStyle.IMAGE and felt is not None
    snapshot["felt"] = {
        "style": "image" if felt_is_image else "color",
        "image": _media_url(felt.image) if felt_is_image else None,
        "color": team.felt_color,
    }
    # 'theme' n'est ni une couleur ni une image : le client ne peint rien et laisse
    # la page suivre le mode clair/sombre. Une image choisie puis desactivee retombe
    # sur la couleur, comme le feutre.
    background_is_image = team.background_style == BackgroundStyle.IMAGE and background is not None
    if team.background_style == BackgroundStyle.THEME:
        background_style = "theme"
    else:
        background_style = "image" if background_is_image else "color"
    snapshot["background"] = {
        "style": background_style,
        "image": _media_url(background.image) if background_is_image else None,
        "color": team.background_color,
    }
    snapshot["theme"] = {"cardBackColor": team.card_back_color, "feltColor": team.felt_color}
    return snapshot
