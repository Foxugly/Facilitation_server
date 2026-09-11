"""Transvase les votes en reponses a un item.

Le sens arriere du RunPython est un noop : `item` et `payload` retombent avec
0013 (AddField), inutile de les vider a la main.

La bascule de contrainte d'unicite (round, participant) -> (item, participant)
part dans 0015, une migration separee. PostgreSQL refuse un ALTER TABLE tant
que des evenements de trigger restent en attente a la suite d'un RunPython qui
ecrit sur la meme table, dans la MEME transaction - sqlite n'a pas cette
contrainte, d'ou un ecart entre suite locale verte et CI rouge. Scinder en deux
migrations donne a chacune sa propre transaction : les ecritures de celle-ci
sont validees avant que 0015 ne touche au schema.
"""
from django.db import migrations

from rooms.migration_ops import votes_to_responses


def forwards(apps, schema_editor):
    votes_to_responses(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('rooms', '0013_response'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
