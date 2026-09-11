"""Transvase les votes en reponses a un item, puis bascule la contrainte
d'unicite de (round, participant) vers (item, participant).

Le sens arriere du RunPython est un noop : `item` et `payload` retombent avec
0013 (AddField), inutile de les vider a la main.

La bascule de contrainte est placee APRES le transvasement (RunPython), pas
avant : la poser plus tot porterait sur des `item` encore nuls. Sans risque
sur les donnees existantes - tant que cette livraison tourne, un round ne
porte qu'un item, donc les deux contraintes sont equivalentes ici.
"""
from django.db import migrations, models

from rooms.migration_ops import votes_to_responses


def forwards(apps, schema_editor):
    votes_to_responses(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('rooms', '0013_response'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='response',
            name='uniq_vote_round_participant',
        ),
        migrations.AddConstraint(
            model_name='response',
            constraint=models.UniqueConstraint(fields=('item', 'participant'), name='uniq_response_item_participant'),
        ),
    ]
