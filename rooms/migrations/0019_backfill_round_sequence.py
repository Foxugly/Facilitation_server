"""Remplit `Round.sequence` sur les rounds existants (tache 1, design 2026-09-12).

Scindee de 0018 (AddField) : PostgreSQL refuse un ALTER TABLE tant que des
evenements de trigger restent en attente a la suite d'un RunPython qui ecrit
sur la meme table, dans la MEME transaction -- sqlite n'a pas cette contrainte,
d'ou un ecart entre suite locale verte et CI rouge (deja rencontre en 0014/0015).
Deux migrations, deux transactions : 0018 valide le schema avant que celle-ci
n'ecrive.
"""
from django.db import migrations

from rooms.migration_ops import backfill_round_sequence


def forwards(apps, schema_editor):
    backfill_round_sequence(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('rooms', '0018_round_sequence'),
    ]

    # Irreversible en pratique : revenir en arriere remettrait tous les rounds
    # a la valeur par defaut (1), pas a un etat ayant jamais existe. AddField
    # (0018) ne connaissait meme pas ce champ en sens inverse.
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
