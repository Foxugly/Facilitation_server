"""Operations de migration de donnees, sorties des fichiers de migration.

Meme motif que `rooms/migration_ops.py` : les fichiers de `migrations/` sont
figes une fois joues, la logique vit ici pour rester lisible et testable. Ces
fonctions ne prennent que le registre `apps` de la migration : elles
n'importent AUCUN modele concret, sans quoi elles casseraient des que le
modele evoluerait.
"""


def reactivate_dot_voting_deck(apps):
    """Rallume un deck `dot_voting` deja seme et reste inactif (brief 6a-7,
    tache 1).

    Le seed (`decks/seed.py::create_dot_voting_deck`) creait ce deck avec
    `is_active=False` tant que le registre d'activites (realtime/activities.py)
    ne connaissait pas "dot_voting" -- une base qui a tourne
    `seed_dot_voting_deck` avant ce commit porte donc peut-etre deja la ligne,
    inactive. Sans cette migration, cette base ne convergerait jamais vers le
    nouveau defaut : le seed s'arrete des qu'une ligne existe, il ne la met
    jamais a jour (regle testee par
    `test_replaying_the_command_never_reverts_a_deliberate_reactivation`).

    Ne touche que le deck du vote_type de code "dot_voting", et seulement s'il
    est encore inactif -- idempotente, sans effet si un operateur l'a deja
    rallume a la main.
    """
    Deck = apps.get_model("decks", "Deck")

    Deck.objects.filter(vote_type__code="dot_voting", is_active=False).update(is_active=True)
