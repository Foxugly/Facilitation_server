# Bypass d'abonnement + gestion des tables

**Date :** 2026-07-18
**Portée :** `Poker_server` / `Poker_frontend` (lots A et B), `trainingmanager_server` / `trainingmanager_frontend` (lot A seulement)

Deux chantiers indépendants, spécifiés ensemble parce qu'ils partagent le même point
d'ancrage : les helpers de droits de `billing/service.py`.

- **Lot A** — un flag `subscription_bypass` sur le compte, qui accorde tous les droits
  payants sans souscription.
- **Lot B** — rendre les tables persistantes d'une équipe utilisables : liste, quota par
  plan, invitation par table, historique par table.

---

## État de l'existant (vérifié)

Les décisions ci-dessous s'appuient sur ces constats, tous relevés dans le code au
2026-07-18.

### Poker

| Fait | Référence |
|---|---|
| `Subscription` est un OneToOne par compte, plans `team1`/`team5` | `billing/models.py:8` |
| Les plans ne sont pas en base : `STRIPE_PRICES` et `PLAN_QUOTAS` sont du settings | `config/settings/base.py:251,261` |
| `PLAN_QUOTAS` code un **nombre d'équipes possédées**, pas un niveau de fonctionnalités | `config/settings/base.py:261` |
| Tous les droits payants passent par 3 helpers, appelés depuis 6 vues | `billing/service.py:52,60,68` |
| Billing **inerte** sans clés Stripe : `user_is_paid()` → `True`, `user_quota()` → 10 000 | `billing/service.py:11,55,62` |
| Aucun abonné réel en base à ce jour (conséquence du point précédent) | — |
| `Room` liée à une `Team` est **déjà non-éphémère** | `rooms/models.py:52` (`is_live`) |
| Le ré-accès d'un membre **réutilise son `Participant`** (pas de siège dupliqué) | `rooms/api_views.py:123` |
| **Aucun endpoint de liste** : seulement create / exists / join, tous par code | `rooms/api_urls.py` |
| Room d'équipe = membres-only, strict | `rooms/api_views.py:121` |
| Le plafond de 20 compte inscrits **et** anonymes | `rooms/api_views.py:126,138` |
| L'historique est un **read model vivant** dérivé de `Result`, sans table de snapshot | `history/api_views.py:38-40` |
| Invitations email : niveau **équipe** uniquement, TTL 7 jours | `teams/models.py:51`, `teams/api_views.py:28` |
| Pas de back-office staff dans le SPA ; seul guard = `auth.guard.ts` | `Poker_frontend/src/app/core/auth/` |

### TrainingManager

| Fait | Référence |
|---|---|
| Aucun abonnement, aucun Stripe. Seul verrou : un entier | `customuser/models.py:101` (`team_quota`, défaut 0) |
| Le help_text prévoit explicitement un « future billing flow » | `customuser/models.py:101-108` |
| Quota appliqué à la création d'équipe | `team/views/teams.py:100-115` |
| Quota exposé dans `/me/` | `customuser/serializers.py:82` |
| `is_staff` / `is_superuser` déjà exposés read-only dans `/me/` | `customuser/serializers.py:35` |
| Back-office staff déjà en place côté SPA | `core/auth/superuser.guard.ts` |

### Alternative écartée : la fausse souscription en base

Insérer à la main une `Subscription` `status="active"`, `current_period_end=2076` fonctionne
sur Poker sans écrire de code — `_active_subscription()` ne teste jamais les IDs Stripe
(`billing/service.py:43-49`). Écartée pour quatre raisons :

1. **Elle ne donne pas l'illimité.** `user_quota()` fait `PLAN_QUOTAS.get(sub.plan, 0)` : un
   plan inventé renvoie 0. Il faudrait de toute façon toucher au code.
2. **Stripe l'écrase silencieusement.** `_sync_from_stripe()` réécrit la même ligne
   (`billing/api_views.py:35`) ; un `customer.subscription.deleted` annulerait le cadeau
   sans trace.
3. **Elle n'existe pas sur TM** (pas de modèle `Subscription`) : elle y dégénère en
   `team_quota = 99999` à la main, soit le bricolage actuel.
4. **Elle n'est ni auditable ni affichable** : impossible de distinguer un compte offert
   d'un payant, ni dans les stats ni dans l'UI.

Elle reste un geste d'ops ponctuel acceptable, mais pas une fondation.

---

## Lot A — flag bypass

### A.1 Modèle

Sur `accounts.User` (Poker) et `customuser.CustomUser` (TM), identiquement :

```python
subscription_bypass = models.BooleanField(
    default=False,
    help_text="Accorde tous les droits payants sans souscription (accès offert).",
)
bypass_note = models.CharField(max_length=200, blank=True)      # « early adopter », « asso X »
bypass_granted_at = models.DateTimeField(null=True, blank=True)
```

`bypass_note` et `bypass_granted_at` sont renseignés par l'admin et n'ont aucun effet
fonctionnel : ils existent pour l'audit. Ils sont mis à jour au moment de la bascule
(cf. A.4), pas laissés à la discipline de l'opérateur.

### A.2 Poker — branchement

Deux court-circuits en tête des helpers existants, aucun appelant modifié :

```python
UNLIMITED = 10_000   # extraction de la constante déjà utilisée l.63

def user_is_paid(user) -> bool:
    if getattr(user, "subscription_bypass", False):
        return True
    if not billing_configured():
        return True
    return _active_subscription(user) is not None

def user_quota(user) -> int:
    if getattr(user, "subscription_bypass", False):
        return UNLIMITED
    if not billing_configured():
        return UNLIMITED
    sub = _active_subscription(user)
    return quota_for_plan(sub.plan) if sub else 0
```

Les 6 appelants (`teams/api_views.py:48,52,87`, `boards/api_views.py:80,101`,
`history/api_views.py:103`) sont inchangés. `team_is_paid()` en hérite gratuitement
puisqu'il délègue à `user_is_paid(team.owner)` : **un owner offert rend toute son équipe
payante**, ce qui est le comportement voulu.

`SubscriptionView` (`billing/api_views.py:113`) gagne `"bypass": user.subscription_bypass`
dans sa réponse, pour que le SPA masque les écrans de tarifs plutôt que d'afficher
« plan : néant, quota : 10000 ».

### A.3 TrainingManager — branchement

Nouveau module `customuser/entitlements.py`, même signature que Poker :

```python
UNLIMITED = 10_000

def user_is_paid(user) -> bool:
    return True          # pas de billing sur TM à ce jour

def user_quota(user) -> int:
    if user.subscription_bypass:
        return UNLIMITED
    return user.team_quota
```

Deux appelants à rediriger vers `user_quota()` : `team/views/teams.py:103` et
`customuser/serializers.py:82`. `CustomUser.can_create_team()` (`customuser/models.py:145`)
renvoie `True` si `subscription_bypass`.

L'intérêt du module : le jour où Stripe est porté sur TM, seul son intérieur change.

### A.4 Administration

Trois surfaces, sur les deux sites.

**Admin Django** — `subscription_bypass`, `bypass_note` dans le fieldset de `UserAdmin`,
plus `list_filter` sur `subscription_bypass`.

**Badge profil (lecture seule)** — `/me/` expose `subscription_bypass` en read-only, comme
`is_staff` l'est déjà côté TM. Le SPA affiche « Accès offert » sur la page Profil et masque
les tarifs.

**Back-office staff dans le SPA** — recherche de compte + bascule du flag :

- `GET /staff/users?q=<email>` → liste paginée (id, email, display_name, `subscription_bypass`, `bypass_note`)
- `PATCH /staff/users/<id>` → `{subscription_bypass, bypass_note}` ; positionne
  `bypass_granted_at = now()` à l'activation, le laisse tel quel à la désactivation.

Les deux endpoints en `permission_classes = [permissions.IsAdminUser]`.

Côté TM l'infra existe (`superuser.guard.ts`, lien back-office) : c'est un écran de plus.
**Côté Poker tout est à créer** : guard superuser, route, lien de nav, écran. C'est le poste
le plus lourd du lot A.

### A.5 Découpage

- **A1** — champs + migrations + branchement Poker & TM + admin Django + badge profil.
  Livrable seul, immédiatement utilisable via l'admin.
- **A2** — endpoints staff + back-office SPA sur les deux sites.

---

## Lot B — tables Poker

### B.0 Vocabulaire

Le modèle reste `Room` (le renommer toucherait consumers, routes WS et SPA pour aucun
gain). L'**UI dit « table »** partout. Mapping : *table* = `Room` liée à une `Team` ;
*round* = `VoteSession`.

Les rooms anonymes sans équipe (éphémères, 8 h) sont **hors périmètre** et restent
strictement inchangées.

### B.1 Liste des tables

Le manque réel : la persistance existe déjà, mais une table est introuvable sans son code.

- `GET /teams/<id>/rooms` — membres uniquement. Par table : `code`, `title`,
  `participantCount`, `lastActivityAt`, sujet en cours, `archivedAt`.
- `PATCH /rooms/<code>` — renommer. Admin d'équipe (`is_admin`).
- `POST /rooms/<code>/archive` — archiver. Admin d'équipe.

Nouveau champ `Room.archived_at` (nullable, indexé). Une table archivée sort de la liste
par défaut et refuse les nouveaux `join` ; elle reste interrogeable en historique.

**Archiver, jamais supprimer.** L'historique étant dérivé live de `Result`
(`history/api_views.py:38`), un `DELETE` cascaderait sur les sessions et effacerait
l'historique de l'équipe. Aucun endpoint de suppression de room n'est exposé.

SPA : écran **Tables** par équipe, une carte par table, bouton *Rejoindre* qui navigue
directement. Un membre n'a plus jamais besoin du code à 6 caractères ; celui-ci reste pour
le partage verbal en atelier.

« Changer de table » n'exige aucune mécanique nouvelle : on clique une autre carte. Le
`RoomConsumer` fait déjà `group_discard` au disconnect (`realtime/consumers.py:31`).

### B.2 Quota de tables par plan

Dans `config/settings/base.py`, à côté de `PLAN_QUOTAS` :

```python
PLAN_ROOM_QUOTAS = {"team1": 1, "team5": 5}   # tables persistantes PAR équipe
```

et dans `billing/service.py`, calqué sur `user_quota()` :

```python
def room_quota(user) -> int:
    if getattr(user, "subscription_bypass", False):
        return UNLIMITED
    if not billing_configured():
        return UNLIMITED
    sub = _active_subscription(user)
    return settings.PLAN_ROOM_QUOTAS.get(sub.plan, 0) if sub else 0
```

Le quota est **par équipe** (« 3/5 tables » sur l'écran d'une équipe), évalué contre
`room_quota(team.owner)` — cohérent avec `team_is_paid()` qui s'appuie déjà sur l'owner.
Appliqué dans `CreateRoomView` uniquement quand `team` est renseigné ; erreur
`room_quota_exceeded` en 402, sur le modèle de `quota_exceeded` (`teams/api_views.py:52`).

Les tables archivées ne comptent pas dans le quota.

**Rétrogradation de plan** (team5 → team1 avec 4 tables) : aucune donnée n'est supprimée ni
archivée d'office. Les tables existantes continuent de vivre ; seule la création est bloquée
jusqu'à redescendre sous le quota.

### B.3 Invitation par table

Nouveau modèle `rooms.RoomInvitation`, calqué sur `teams.Invitation` :

```python
room = models.ForeignKey("rooms.Room", on_delete=models.CASCADE, related_name="invitations")
email = models.EmailField()
token = models.CharField(max_length=64, unique=True)     # secrets.token_urlsafe
expires_at = models.DateTimeField()                       # +7 jours, même TTL qu'équipe
accepted_at = models.DateTimeField(null=True, blank=True)
created_by = models.ForeignKey(AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
created_at = models.DateTimeField(auto_now_add=True)
```

- `POST /rooms/<code>/invitations` — admin d'équipe. Envoie l'email.
- `GET /rooms/<code>/invitations` — admin d'équipe, invitations en cours.
- `DELETE /rooms/<code>/invitations/<id>` — révocation.

L'email porte `/join/<CODE>?invite=<token>`. Sur la page d'atterrissage, **c'est l'invité
qui choisit** : se connecter / créer un compte (magic link existant), ou continuer en invité
avec un nom d'affichage. L'invitant ne décide pas à l'avance — la différence de droits
découle mécaniquement de `Participant.user` (null ou non), ce qui évite un champ
« type d'invitation » et sa logique de cohérence.

Dans `JoinRoomView`, un token valide et non expiré **remplace** le contrôle `is_member`
(`rooms/api_views.py:121`) : c'est le nouveau droit d'accès par room. Le token reste
utilisable jusqu'à expiration, pour que l'invité anonyme revienne à la séance suivante ; son
siège persiste de toute façon via le `participantToken` déjà stocké côté client.
`accepted_at` est renseigné à la première utilisation, à titre informatif.

Le plafond de 20 est déjà correct et ne change pas : `room.participants.count()` compte
indifféremment inscrits et anonymes.

### B.4 Capacités : anonyme vs compte

Deux restrictions, **uniquement dans les rooms d'équipe**. Les rooms anonymes gardent leur
facilitateur anonyme, sans quoi elles cesseraient de fonctionner.

- **Facilitation** — `facilitator.claim` et `facilitator.transfer` (`realtime/consumers.py`)
  refusent une cible dont `user_id` est `None` quand `room.team_id` est non-null. Erreur
  protocole explicite, jamais un échec muet.
- **Historique** — réservé aux comptes, automatique puisque les vues sont `IsAuthenticated`.

### B.5 Historique par table

Le read model actuel filtre `session__room__team=team` (`history/api_views.py:40`). On
ajoute un scope par rooms, en factorisant `_entries_for` pour accepter un queryset de rooms.

- `GET /history/rooms/` — jours où l'utilisateur a de l'historique
- `GET /history/rooms/<day>/` — résultats de ce jour

Les rooms retenues sont celles où `request.user` possède un `Participant` — donc **toutes
les tables auxquelles il est lié**, y compris dans plusieurs équipes. Les endpoints équipe
existants restent inchangés pour les membres.

### B.6 Découpage

- **B1** — liste + renommer + archiver (`archived_at`). Le manque réel, livrable seul.
- **B2** — `PLAN_ROOM_QUOTAS` + `room_quota()` + application à la création.
- **B3** — `RoomInvitation` + emails + page d'atterrissage + restrictions anonyme (B.4).
- **B4** — historique par table.

Ordre imposé : B1 avant B2 (le quota doit être visible quelque part), B3 avant B4 (le
scope par participant n'a d'intérêt qu'une fois les invités présents).

---

## Tests

Chaque lot est couvert au niveau de la règle métier, pas de l'implémentation.

**Lot A** — `user_is_paid()` / `user_quota()` avec bypass on/off × billing configuré/inerte
(4 cas chacun) ; un owner bypass rend `team_is_paid()` vrai pour son équipe ; `/me/` et
`SubscriptionView` exposent le flag ; les endpoints staff refusent un non-staff en 403 ;
la bascule positionne `bypass_granted_at`. Mêmes tests côté TM sur `entitlements.py` et
`can_create_team()`.

**Lot B** — la liste ne renvoie que les rooms de l'équipe et exclut les archivées ; un
non-membre reçoit 403 ; la création au-delà du quota renvoie 402 `room_quota_exceeded` et
les archivées ne comptent pas ; une rétrogradation ne supprime rien ; un token
d'invitation valide permet le join d'un non-membre, un token expiré ou révoqué non ;
le 21ᵉ participant est refusé quel que soit son type ; un participant anonyme ne peut ni
réclamer ni recevoir la facilitation dans une room d'équipe, mais le peut dans une room
anonyme ; l'historique par rooms ne renvoie que les tables où l'utilisateur a un
`Participant`.

## Points hors périmètre

- Porter Stripe sur TrainingManager (le module `entitlements.py` prépare le terrain, rien
  de plus).
- Le scénario « atelier scindé en sous-groupes » (déplacer des participants entre tables en
  cours de séance) : écarté, ce n'est pas l'usage visé.
- Renommer `Room` en `Table` dans le code.
- Toute modification des rooms anonymes éphémères.
