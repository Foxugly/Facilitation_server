"""``VoteSession`` → ``Round`` (vocabulaire de domaine Facilitation).

Écrite à la main : ``makemigrations`` pose des questions interactives sur les
renommages et, sans réponse, produirait un DeleteModel + CreateModel qui
détruirait les rounds, votes et résultats existants.

Ordre : la contrainte unique porte sur ``Vote.session``. Elle est retirée AVANT
le renommage du champ et reposée après, sinon PostgreSQL la voit référencer une
colonne qui n'existe plus sous ce nom.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rooms", "0008_room_result_layout"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="vote",
            name="uniq_vote_session_participant",
        ),
        migrations.RenameModel(
            old_name="VoteSession",
            new_name="Round",
        ),
        migrations.RenameField(
            model_name="room",
            old_name="current_session",
            new_name="current_round",
        ),
        migrations.RenameField(
            model_name="vote",
            old_name="session",
            new_name="round",
        ),
        migrations.RenameField(
            model_name="result",
            old_name="session",
            new_name="round",
        ),
        # ``related_name`` ne touche pas le schéma, mais vit dans l'état des
        # migrations : sans ces deux AlterField, ``makemigrations --check`` réclame
        # indéfiniment une migration de plus.
        migrations.AlterField(
            model_name="round",
            name="room",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rounds",
                to="rooms.room",
            ),
        ),
        migrations.AlterField(
            model_name="round",
            name="subject",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="rounds",
                to="rooms.subject",
            ),
        ),
        migrations.AddConstraint(
            model_name="vote",
            constraint=models.UniqueConstraint(
                fields=("round", "participant"), name="uniq_vote_round_participant"
            ),
        ),
    ]
