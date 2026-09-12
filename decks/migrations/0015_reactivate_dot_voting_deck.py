"""Rallume le deck `dot_voting` deja seme et reste inactif (brief 6a-7, tache 1).

Migration de DONNEES pure -- aucun changement de schema ici, donc pas le piege
PostgreSQL/sqlite deja rencontre ailleurs dans ce depot (rooms 0014/0015,
0018/0019) qui force a scinder un RunPython d'un ALTER TABLE sur la meme
table dans deux migrations/transactions separees.
"""
from django.db import migrations

from decks.migration_ops import reactivate_dot_voting_deck


def forwards(apps, schema_editor):
    reactivate_dot_voting_deck(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('decks', '0014_background'),
    ]

    # noop en sens inverse, pas un reverse_code qui desactiverait : entre cette
    # migration et un eventuel "unmigrate", un operateur peut avoir rallume (ou
    # laisse) le deck pour une raison independante de CETTE migration -- le
    # forcer a redevenir inactif violerait la meme regle que le seed respecte
    # deja (ne jamais revenir sur une reactivation deliberee, voir
    # test_replaying_the_command_never_reverts_a_deliberate_deactivation dans
    # rooms/tests/test_dot_voting_deck.py). Symetrique au choix deja fait sur
    # 0019_backfill_round_sequence pour la meme raison de fond.
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
