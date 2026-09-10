"""Convertit les couches ``icon`` d'une cle en dur vers une image televersee.

Jusqu'ici une couche ``icon`` stockait une cle (« fist-3 ») que le frontend resolvait
dans un registre code en dur : ajouter un pictogramme demandait une livraison du
frontend, et une cle inconnue donnait une carte vide, sans le moindre message. Le
pictogramme devient une donnee comme tous les autres visuels de l'application.

La cle vaut exactement le nom du fichier, d'ou la conversion directe. Les fichiers
correspondants doivent etre presents dans le stockage media — sans quoi la carte
s'affiche sans pictogramme, mais rien ne casse.
"""
from django.db import migrations

ICON_DIR = "decks/icons"


def key_to_image(apps, schema_editor):
    TextLayer = apps.get_model("decks", "TextLayer")
    Translation = apps.get_model("decks", "TextLayerTranslation")

    for layer in TextLayer.objects.filter(content_kind="icon"):
        if layer.image:
            continue
        key = (
            Translation.objects.filter(master_id=layer.pk)
            .values_list("content", flat=True)
            .first()
        )
        if not key:
            continue
        layer.image = f"{ICON_DIR}/{key}.png"
        layer.save(update_fields=["image"])
        # Le texte n'a plus de sens sur une couche icon : le laisser ferait croire
        # a l'admin qu'il pilote encore quelque chose.
        Translation.objects.filter(master_id=layer.pk).update(content="")


def image_to_key(apps, schema_editor):
    """Retour en arriere : le nom de fichier redevient la cle."""
    TextLayer = apps.get_model("decks", "TextLayer")
    Translation = apps.get_model("decks", "TextLayerTranslation")

    for layer in TextLayer.objects.filter(content_kind="icon").exclude(image=""):
        key = layer.image.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        rows = Translation.objects.filter(master_id=layer.pk)
        if rows.exists():
            rows.update(content=key)
        else:
            Translation.objects.create(master_id=layer.pk, language_code="en", content=key)


class Migration(migrations.Migration):

    dependencies = [
        ("decks", "0012_textlayer_icon_image"),
    ]

    operations = [
        migrations.RunPython(key_to_image, image_to_key),
    ]
