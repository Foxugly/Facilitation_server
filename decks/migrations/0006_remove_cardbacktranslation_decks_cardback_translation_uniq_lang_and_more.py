"""CardBack.name stops being a parler field.

Done in two steps because parler already exposes a ``name`` accessor on the master
model while the translation table exists: adding a concrete ``name`` at the same
time raises "already has a field named 'name'". So the data lands in a temporary
column here, and 0007 renames it once the translations are gone.
"""
from django.db import migrations, models

# Order of preference when collapsing the translated rows into the single field.
LANG_PREFERENCE = ("en", "fr")


def collapse_translations(apps, schema_editor):
    """Carry each back's translated name over before the translation table goes.

    Without this the existing names would be dropped silently.
    """
    CardBack = apps.get_model("decks", "CardBack")
    Translation = apps.get_model("decks", "CardBackTranslation")
    by_master = {}
    for row in Translation.objects.all():
        by_master.setdefault(row.master_id, {})[row.language_code] = row.name
    for back in CardBack.objects.all():
        names = by_master.get(back.pk, {})
        if not names:
            continue
        chosen = next((names[lang] for lang in LANG_PREFERENCE if names.get(lang)), None)
        back.legacy_name = chosen or next(iter(names.values()))
        back.save(update_fields=["legacy_name"])


def restore_translations(apps, schema_editor):
    CardBack = apps.get_model("decks", "CardBack")
    Translation = apps.get_model("decks", "CardBackTranslation")
    for back in CardBack.objects.exclude(legacy_name=""):
        Translation.objects.get_or_create(
            master_id=back.pk, language_code="en", defaults={"name": back.legacy_name}
        )


class Migration(migrations.Migration):

    dependencies = [
        ('decks', '0005_cardback_free_tier_deck_free_tier'),
    ]

    operations = [
        migrations.AddField(
            model_name='cardback',
            name='legacy_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.RunPython(collapse_translations, restore_translations),
        migrations.RemoveConstraint(
            model_name='cardbacktranslation',
            name='decks_cardback_translation_uniq_lang',
        ),
        migrations.DeleteModel(
            name='CardBackTranslation',
        ),
    ]
