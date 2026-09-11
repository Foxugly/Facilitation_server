from django.db import migrations

from rooms.migration_ops import subjects_to_items


def forwards(apps, schema_editor):
    subjects_to_items(apps)


class Migration(migrations.Migration):
    dependencies = [("rooms", "0010_item")]

    # Irreversible en pratique : revenir en arriere supposerait de deviner quels
    # rounds ont ete crees pour des subjects orphelins. Les items retombent de
    # toute facon avec la table, supprimee par 0010 en sens inverse.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
