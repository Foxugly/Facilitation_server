"""Bascule la contrainte d'unicite de Response : (round, participant) ->
(item, participant).

Separee de 0014 a dessein : 0014 ecrit sur `rooms_response` via RunPython,
et PostgreSQL refuse un ALTER TABLE sur une table qui a encore des
evenements de trigger en attente issus d'ecritures de la meme transaction.
En isolant cette bascule dans sa propre migration, ses ecritures amont sont
deja validees (transaction 0014 commitee) avant que celle-ci n'ouvre la
sienne pour toucher au schema.

Sans risque sur les donnees existantes - tant que cette livraison tourne, un
round ne porte qu'un item, donc les deux contraintes sont equivalentes ici.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rooms', '0014_votes_to_responses'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='response',
            name='uniq_vote_round_participant',
        ),
        migrations.AddConstraint(
            model_name='response',
            constraint=models.UniqueConstraint(fields=('item', 'participant'), name='uniq_response_item_participant'),
        ),
    ]
