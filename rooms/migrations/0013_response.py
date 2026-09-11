"""``Vote`` -> ``Response`` (une reponse porte desormais un item et un payload).

RenameModel ecrit a la main : makemigrations proposait un DeleteModel +
CreateModel (les changements de related_name faisaient perdre a l'heuristique
de detection la similarite avec le modele existant). Un DeleteModel aurait
detruit tous les votes en base de production ; RenameModel preserve les
lignes existantes.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rooms', '0012_drop_subject'),
    ]

    operations = [
        migrations.RenameModel(old_name='Vote', new_name='Response'),
        migrations.AlterField(
            model_name='response',
            name='round',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='responses', to='rooms.round'),
        ),
        migrations.AlterField(
            model_name='response',
            name='card_value',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='response',
            name='item',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='responses', to='rooms.item'),
        ),
        migrations.AddField(
            model_name='response',
            name='payload',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
