# Plan — upload de catalogue (decks / dos / feutres) avec propriété par « squad »

> **Statut : plan documenté, NON implémenté** (décidé avec Renaud le 2026-07-19).
> À ne coder que le jour où l'on ouvre l'upload utilisateur. Le filtre de sélection
> est trivial ; le vrai travail est le flux d'upload + la validation d'images.

## Contexte

Les modèles de catalogue (`decks.Deck`, `decks.CardBack`, `decks.Felt`) n'ont **plus**
de FK de propriété (`team` retiré, PR #12) : c'était du poids mort tant qu'aucun flux
d'upload n'existait. La visibilité repose aujourd'hui sur `free_tier` seul (salle
anonyme = subset gratuit ; équipe = tout le catalogue standard).

Quand l'upload arrivera, la propriété se fera **par utilisateur**, pas par équipe.

## Modèle de propriété

Chaque entrée custom porte :

```python
uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
# null = entrée standard/livrée ; non-null = uploadée par cet utilisateur
```

à ajouter sur `Deck`, `CardBack` **et** `Felt`.

## La « squad » (décisions Renaud, 2026-07-19)

Pour une équipe `T` d'owner `o1`, le catalogue custom **visible au choix** = les
entrées dont `uploaded_by ∈ squad(o1)`, avec :

    squad(o1) = { o1 } ∪ { managers de toutes les équipes que o1 possède }

Décisions figées :

1. **owner + managers uniquement** (pas les simples members).
2. **Un upload est visible partout où son auteur est dans une squad.** Un manager qui
   gère des équipes pour deux owners différents → son upload apparaît dans les deux
   squads. Assumé, pas de rattachement à un owner unique.
3. **`team.card_back` / `team.felt` restent des FK uniques** (l'élément *choisi*) et
   `team.decks` reste un M2M (les *activés*). La squad **alimente la liste de choix** ;
   l'équipe n'en applique qu'un (dos/feutre) ou plusieurs (decks). Direction inchangée :
   Team → catalogue. On ne réintroduit **pas** de FK catalogue → Team ni → User côté
   *choix* ; `uploaded_by` sert uniquement à la propriété/au filtrage.

## Changement de sélection (la partie facile)

Dans `decks/selection.py`, `available_decks` / `available_card_backs` / `available_felts`
prennent, pour une équipe, le catalogue standard **plus** les customs de la squad :

```python
def squad_of(owner):
    from teams.models import Team, TeamMembership, TeamRole
    team_ids = Team.objects.filter(owner=owner).values_list("pk", flat=True)
    manager_ids = TeamMembership.objects.filter(
        team_id__in=team_ids, role=TeamRole.MANAGER
    ).values_list("user_id", flat=True)
    return {owner.pk, *manager_ids}

# available_decks(team) :
#   base standard (uploaded_by__isnull=True)  ∪  Deck.objects.filter(uploaded_by__in=squad_of(team.owner))
```

Anonyme (`team=None`) : inchangé, `free_tier=True` et rien de custom.

## Le vrai travail : l'upload

- Endpoint d'upload (manager+, gated payant) posant `uploaded_by = request.user`.
- **Validation serveur stricte** des images — cf. `docs/card-assets-spec.md` §82-84
  (type réel, taille, dimensions, ré-encodage). C'est le point sérieux, pas le filtre.
- Quota éventuel par utilisateur/compte (à décider).
- Front : bouton « Créer un jeu » / upload de dos/feutre aujourd'hui désactivé, à activer.
- Le badge `is_custom` du serializer (aujourd'hui toujours `False`) redeviendra
  `uploaded_by_id is not None`.

## Points ouverts (à trancher au moment de coder)

- Quota d'uploads.
- Un owner peut-il conserver *plusieurs* dos custom et basculer ? Aujourd'hui
  `team.card_back` = un seul ; le filtre squad donne déjà le choix parmi plusieurs,
  donc rien à changer sauf si on veut une bibliothèque perso hors équipe.
- Suppression d'un utilisateur (`on_delete=SET_NULL`) → l'entrée devient orpheline
  (visible de personne via squad). Comportement acceptable ou faut-il réassigner ?
