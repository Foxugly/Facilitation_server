"""Second half of the parler removal: the temporary column becomes ``name``.

Split from 0006 because the concrete field can only be called ``name`` once the
translation table (and parler's accessor of the same name) is gone.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('decks', '0006_remove_cardbacktranslation_decks_cardback_translation_uniq_lang_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='cardback',
            old_name='legacy_name',
            new_name='name',
        ),
    ]
