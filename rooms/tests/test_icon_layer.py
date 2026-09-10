"""Une couche `icon` dessine une IMAGE televersee, pas une cle resolue en dur cote
client : ajouter un pictogramme doit etre un televersement, jamais une livraison du
frontend. Le snapshot doit donc porter son URL."""
import pytest

from decks.models import TextLayer, TextLayerKind
from rooms.snapshot import build_deck_snapshot


@pytest.mark.django_db
def test_icon_layer_carries_its_image_url(standard_deck):
    card = standard_deck.cards.order_by("order").first()
    TextLayer.objects.create(
        card=card, order=9, pos_x=50, pos_y=50, font_size=55,
        content_kind=TextLayerKind.ICON, image="decks/icons/fist-3.png",
    )

    snapshot = build_deck_snapshot(standard_deck)
    icon = next(l for l in snapshot["cards"][0]["layers"] if l["kind"] == "icon")

    assert icon["icon"].endswith("/decks/icons/fist-3.png")
    assert icon["icon"].startswith("http")


@pytest.mark.django_db
def test_text_layers_carry_no_icon(standard_deck):
    """Une couche de texte ne doit pas se voir attribuer de pictogramme fantome."""
    snapshot = build_deck_snapshot(standard_deck)
    for layer in snapshot["cards"][0]["layers"]:
        if layer["kind"] != "icon":
            assert layer["icon"] is None


@pytest.mark.django_db
def test_i18n_layer_still_serialises_as_a_dict(standard_deck):
    """Garde-fou de non-regression : le comportement existant ne bouge pas."""
    snapshot = build_deck_snapshot(standard_deck)
    layers = snapshot["cards"][0]["layers"]
    i18n = next(l for l in layers if l["kind"] == "i18n")

    assert isinstance(i18n["text"], dict)
    assert i18n["text"]["fr"] == "Dire"
