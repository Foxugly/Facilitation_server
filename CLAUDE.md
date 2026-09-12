# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Lire ce document comme deux couches.** « Existant » décrit le code tel qu'il est
> aujourd'hui et se vérifie dans les fichiers cités. « Cible » décrit des décisions
> actées mais **pas encore implémentées**. Ne jamais traiter une ligne « Cible » comme
> une description du code : plusieurs la contredisent frontalement (voir §Écarts connus).

---

## Ce qu'est ce dépôt

Fork de `Foxugly/Poker_server` (commit unique `91a960d`, 2026-09-10). **Poker_server
continue de vivre en production : ce dépôt ne le remplace pas et ne doit jamais être
poussé vers lui.**

Cible : `facilitation.foxugly.com`, une **Collaborative Facilitation Toolbox** — plateforme
d'ateliers collaboratifs temps réel pour équipes Agile.
Principe produit : `Create a room → Invite your team → Facilitate → Decide`.

**Delegation Poker n'est plus le produit : c'est la première activité du produit.**

Backend Django + DRF + **Django Channels** sur `facilitation-api.foxugly.com`.
Frontend : `Foxugly/Facilitation_frontend` (Angular 21, standalone + signals, PrimeNG 21).

Le fork n'a renommé que l'identité d'infrastructure — aucun modèle, aucune vue, aucune
logique métier n'a été touchée. `FORK.md` fait foi sur ce qui a été renommé et ce qui a
été laissé intact délibérément.

**Exception de flotte :** seul site Foxugly tournant en **ASGI (daphne)** et non
gunicorn/WSGI, parce que Channels l'exige. La brique temps réel est isolée dans
`realtime/` + `config/asgi.py`. Conventions de flotte : `foxugly-ops/OPERATIONS.md`.

## Vocabulaire — non négociable

| Terme | Sens | État |
|---|---|---|
| `Room` | l'atelier / la réunion. Persiste sur toute la séance. | existe |
| `Round` | une activité lancée dans la room. | **existe** (ex-`VoteSession`, migration 0009) |
| `Item` | un sujet manipulé par une activité. | **existe** (migrations 0010-0012) |
| `Response` | la contribution d'un participant à un item. | **existe** (ex-`Vote`, migrations 0013-0016) |

**Le mot « Session » est banni du domaine.** Il entrait en collision frontale avec l'ancien
`VoteSession`. Ne jamais l'introduire, même en commentaire. Le code en est désormais purgé
(migration `0009_votesession_to_round`) à **une exception près, volontaire** : le type de
message WebSocket `session.join`, qui appartient au contrat (§4) et dont le renommage
casserait `Facilitation_frontend`. Le renommer suppose de livrer les deux dépôts ensemble.

**Convention :** les attributs de modèle s'appellent `round` (`Response.round`, `Result.round`,
`Room.current_round`), mais les **variables locales s'appellent `rnd`** — `round` masquerait
le builtin Python, que `services.set_timer` utilise réellement.

**Contenu produit vs marque projet.** Le code `VoteType` `delegation_poker`, les decks,
`seed_delegation_deck`, les composants `shared/ui/delegation-*` et `public/i18n/*.json`
sont du **contenu**, pas de la marque. Ne pas y toucher sans décision explicite — ne pas
« finir le renommage » dessus.

---

## Commandes

Pas de `.venv` dans ce fork : le créer d'abord (convention de l'espace de travail,
Python 3.14 à `C:\Users\Renaud\AppData\Local\Python\pythoncore-3.14-64\python.exe`).

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver     # daphne/ASGI (daphne est 1er dans INSTALLED_APPS)
```

```powershell
.\.venv\Scripts\python.exe -m pytest                              # suite complète — référence : 381 passed
.\.venv\Scripts\python.exe -m pytest realtime/tests/test_timer.py # un fichier
.\.venv\Scripts\python.exe -m pytest realtime/tests/test_timer.py::test_nom -x
.\.venv\Scripts\python.exe -m pytest -k "reveal and not deck"
```

`pytest.ini` fixe `DJANGO_SETTINGS_MODULE=config.settings.test` et `asyncio_mode = auto`
(les tests du consumer sont async et n'ont pas besoin de `@pytest.mark.asyncio`).
`conftest.py` expose la fixture `standard_deck` via `decks.seed.create_standard_deck`.

**Rejouer la suite sur PostgreSQL** (comme la CI, à faire avant tout push touchant au
schéma). Variables passées à la volée — ne pas écrire de `.env`, qui basculerait aussi
`runserver` sur Postgres en permanence :

```powershell
$env:DB_ENGINE="postgresql"; $env:DB_NAME="facilitation"; $env:DB_HOST="127.0.0.1"
$env:DB_PORT="5432"; $env:DB_USER="facilitation"; $env:DB_PASSWORD="<mdp local>"
.\.venv\Scripts\python.exe -m pytest -q
```

Le rôle a besoin de `CREATEDB` : `pytest-django` crée et détruit `test_facilitation` à
chaque exécution. Poste de dev en **PG 18**, CI et prod en **PG 16** — proche, pas
identique : la CI reste le juge.

```powershell
.\.venv\Scripts\python.exe manage.py seed_delegation_deck   # idempotent, joué par deploy.sh
.\.venv\Scripts\python.exe manage.py seed_icon_decks
.\.venv\Scripts\python.exe manage.py createsuperuser        # email seul, pas de username
```

## Dispatch des settings

`config/settings/` est un **package**, pas un module. `config/settings/__init__.py` choisit
l'environnement à l'import depuis `DJANGO_ENV` (`prod`/`test`) ou `STATE` (`PROD`), avec
`dev` par défaut. `manage.py` / `asgi.py` / `celery.py` pointent tous sur
`DJANGO_SETTINGS_MODULE=config.settings` — jamais un sous-module concret. Tout le réel est
dans `base.py` ; `dev`/`test`/`prod` sont de fines surcharges.

- **dev** : sqlite, `InMemoryChannelLayer` (pas de Redis), `CELERY_TASK_ALWAYS_EAGER`.
- **test** : email locmem, hasher MD5, `PARLER_ENABLE_CACHING = False`, throttles relevés à
  `100000/min`. Ces deux désactivations existent parce que les caches LocMem **survivent au
  rollback DB par test** : parler resservirait la traduction d'un test précédent sur une PK
  réutilisée, et des register/login répétés déclencheraient de faux 429. Conserver **toutes**
  les clés de scope en éditant (`ScopedRateThrottle` lève `KeyError` sur une clé manquante).
- **prod** : PostgreSQL (convention flotte `DB_*` à 6 variables), Channels sur Redis, HSTS,
  `SECURE_PROXY_SSL_HEADER` (nginx termine le TLS).

Sentry ne s'initialise que si l'environnement est prod **et** que le `SENTRY_ENVIRONMENT`
résolu est lui-même un marqueur de production — double garde délibérée, commentée dans `base.py`.

---

# Existant

## Frontière HTTP ↔ WebSocket

HTTP ne fait que ce qui doit précéder l'ouverture du socket ; tout ce qui se passe *dans*
la room passe par le WS.

- `POST /api/v1/rooms` → crée la room, fige le snapshot de deck, renvoie un
  `participantToken` secret.
- `GET /api/v1/rooms/<code>` et `POST …/join` → résolution / entrée, émission du token.
- `ws/rooms/<CODE>/` (`realtime/routing.py`) → tout le reste.

`rooms.api_urls` est monté à la racine de `api/v1/` et **doit rester en dernier** dans
`config/urls.py`, sinon il avale `auth/`, `teams/`, `decks/`… (le fichier le dit en commentaire).

## Quatre couches dans la brique temps réel

1. `realtime/consumers.py` — `RoomConsumer`, mince. Valide l'enveloppe versionnée
   (`v == PROTOCOL_VERSION`, `type`, `payload`, `cid`), dispatche, et **rediffuse le fait**.
   Chaque appel domaine est enrobé dans `database_sync_to_async`. Les tâches de révélation à
   échéance vivent dans un dict `_timer_tasks` **au niveau du module**, pas sur l'instance :
   elles doivent survivre à la déconnexion du client qui a ouvert le vote.
2. `realtime/services.py` (882 lignes, le vrai cœur) — logique de domaine **synchrone et
   pauvre en framework** : machine à états, autorité, décomptes, `build_state_sync`. Gardée
   sync pour être testable sans socket. Un coup illégal lève
   `RoomError(code, message, rejected_type)` au lieu de s'appliquer.
3. `realtime/activities.py` — registre d'activités embryonnaire (`ActivitySpec` : schéma de
   payload, agrégateur, valeur jouable, par `resolution_strategy`). `services.py` route déjà
   dessus (`cast_response`, `revealed_payload`), mais le registre ne porte pas encore tout ce
   que la cible prévoit (`config_schema`, `produces`/`consumes`, `items_authored_by` — 5c).
4. `rooms/models.py` — persistance.

Le serveur fait autorité : les clients émettent des **intentions**, le serveur valide et
diffuse le **fait**. Les intentions de contrôle passent par `_require_facilitator`. Les
valeurs de vote restent secrètes jusqu'au reveal — `revealed_payload` n'émet simplement
aucune clé `votes` sur un round anonyme, ce qui préserve l'invariant sans effort jusque
dans `build_state_sync`.

**Machine à états actuelle :** `RoundState` = `idle → open → revealed → acted` sur `Round`.

## Snapshots de deck — la règle d'immuabilité centrale

`decks/` est un **référentiel éditable** (`VoteType` / `Deck` / `Card` / `TextLayer` /
`CardBack` / `Felt` / `Background`), traductions en lignes django-parler indexées par
`language_code` — jamais des colonnes `label_fr`. Ajouter une langue = une entrée dans
`LANGUAGES` de `base.py`, sans migration.

À la création d'une room, `rooms/snapshot.py` fige ce référentiel dans un blob JSON
immuable : `Room.deck_snapshot` (le deck en jeu) et `Room.deck_snapshots` (tous les decks
que cette room peut jouer). **La couche temps réel ne lit que le blob, jamais les tables
`decks`** : un admin qui édite un deck ne peut donc pas muter une room vivante. Un round
fige en plus `Round.deck_snapshot`, pour qu'un changement de deck en cours de partie
ne réétiquette jamais un résultat antérieur.

Conséquence : toucher à la forme d'une carte ou d'un deck implique *trois* lecteurs — l'ORM,
le constructeur de snapshot, et tout le JSON déjà figé en base.

`decks/selection.py` est la **source unique** de « quels decks une équipe peut jouer »,
partagée par l'API teams et la création de room pour que le catalogue et la distribution ne
divergent pas. `squad_of(owner)` définit la visibilité des uploads : le propriétaire plus
les managers des équipes qu'il possède.

## Comportement routé par `resolution_strategy`

Principe P1 de la spec : *la DB décrit un type de vote, le code décide du comportement.*
`realtime/activities.py::ACTIVITY_REGISTRY` (clé = `VoteType.resolution_strategy`) déclare,
par `ActivitySpec`, le drapeau `ordinal` qui conditionne l'écart min/max — un deck non ordinal
(vote romain, fist-of-five) renvoie `{min: None, max: None}` au lieu de calculer un « 0 – 0 »
de faux consensus à partir des seules valeurs qui passent `isdigit()`. Une stratégie absente du
registre retombe sur `DEFAULT_SPEC` (non ordinal, prudent).

C'est ce registre qui rend la cible atteignable : **Planning Poker sera un `VoteType` et un
deck Fibonacci, pas du nouveau code.**

## Deux sens différents du mot « rôle »

- `rooms.Role` (`facilitator` / `voter`) — **par round**, qui pilote le round courant ;
  se transmet à chaud, protégé par `FACILITATOR_GUARD_SECONDS` avant l'ouverture du takeover.
- `teams.TeamRole` (`owner` / `manager` / `member`) — **administration d'équipe** (membres,
  invitations, réglages, board, historique) via `teams/permissions.py`.

Sans rapport l'un avec l'autre. Un manager peut ne jamais faciliter ; un facilitateur peut
n'être qu'un simple membre.

## Identité des participants

`Participant.token` est le secret rejoué à chaque (re)connexion WS ;
`Participant.public_id` est l'UUID diffusé aux autres. **Ne jamais laisser le token entrer
dans un payload de diffusion.** `display_name` est un nom d'affichage **éphémère, pas un
identifiant d'authentification** : ne jamais le mapper sur un `username` authentifiant.

Côté comptes : `AUTH_USER_MODEL` est figé depuis la migration 0001 sur une base vierge et ne
doit jamais être échangé. Il n'y a **pas de champ `username`** (`USERNAME_FIELD="email"`, §3.16).

## Facturation déléguée, et inerte par défaut

Aucune clé Stripe ici. `billing/client.py` est le **seul** point de sortie vers
`billing-api.foxugly.com` (HMAC-SHA256 dans les deux sens) et sépare les pannes
(`BillingUnavailable`, réessayable → 503) des refus porteurs de sens (`BillingRefused`).
`billing/service.py` lit un cache de droits poussé par le central. `Subscription` est au
niveau **compte**, pas équipe.

Tout est **gaté sur `BILLING_BASE_URL` + `BILLING_APP_SECRET`** : tant qu'ils sont absents,
la facturation est inerte, les équipes restent ouvertes et le checkout répond 503. Garder
toute nouvelle fonction payante derrière `billing.service.paid_required` / `user_is_paid` /
`team_is_paid` pour que ça reste vrai. `BILLING_APP_SLUG` vaut désormais `facilitation` — le
slug doit être enregistré auprès du central, sinon aucun droit payant ne se résout.

## Carte des apps

| App | Rôle |
|---|---|
| `accounts` | `User` email seul, JWT, magic link, confirmation, reset, Turnstile, Graph mail, API staff. |
| `teams` | Équipes, membres, invitations, apparence (tapis / dos de carte / fond / mise en page du dépouillement). |
| `decks` | Référentiel éditable + traductions parler + commandes de seed + visibilité des uploads. |
| `rooms` | Modèles runtime, API HTTP de création, constructeur de snapshot, tâche d'expiration 8h. |
| `realtime` | Consumer Channels + services de domaine synchrones. |
| `history` | Read model dérivé en direct des `Result` actés (pas de table de snapshot) ; lecture réservée aux membres, envoi email aux managers. |
| `boards` | Delegation Board par équipe (niveau AS-IS / TO-BE par domaine de décision). |
| `billing` | Client + gating sur cache de droits contre le service central. |
| `health` | `/health/` liveness + `SELECT 1` (UptimeRobot vérifie `"status": "ok"`). |

`docs/superpowers/specs/` fait référence : contrat temps réel et spec de modèle de données.
**Tout nouvel event étend ce contrat, ne le double pas.**

---

# Cible — décisions actées, non implémentées

## Modèle

- `Room` → `Round` (N par room, séquentiels) → `Item` (N par round) → `Response`.
- **Un seul modèle `Response`** pour toutes les activités, `payload` JSON validé par le
  schéma que déclare le type d'activité. **Jamais une table par activité.**
- `Response.participant` est **toujours** renseigné. L'anonymat est une politique
  d'affichage appliquée **côté serveur** (le serveur n'émet pas la clé `votes`), jamais un
  masquage côté Angular. *(Déjà vrai aujourd'hui — voir `revealed_payload`.)*
- Le mode d'anonymat est **figé au passage en OPEN** et annoncé aux votants avant qu'ils
  votent. *(Déjà vrai : `Round.is_anonymous`.)*
- `Result` est **figé au reveal**, jamais recalculé : il devient l'input d'une autre
  activité et l'historique doit rester stable.

## Registre d'activités

Chaque type déclare : schéma de config, schéma de payload, agrégateur (Responses → Results),
ce qu'il produit / consomme (`items` / `results` / rien), les états qu'il utilise, ses
composants Angular facilitateur et participant.

**Objectif structurant : ajouter une activité ne doit toucher que le registre.**

## États

`DRAFT → READY → OPEN → CLOSED → REVEALED → COMPLETED`, chaque type déclarant ceux qu'il
utilise. Brainstorming et Affinity Mapping s'arrêtent à CLOSED.

L'existant est `idle → open → revealed → acted`, **sans CLOSED**. **Ne pas casser ce cycle
pour le poker** : n'introduire CLOSED que pour les nouvelles activités.

- `REVEALED → OPEN` autorisé (réouverture). Le `Result` est invalidé ; les chaînages déjà
  effectués ne sont **pas** mis à jour — le signaler dans l'UI.
- Passer au round suivant ferme le courant.
- Un seul round actif à la fois (`Room.current_round`).

## Chaînage — le différenciateur produit

Les résultats d'une activité deviennent les items de la suivante (`Send to Dot Voting`,
`Keep Top 3`, `Send to Roman Vote`).

C'est une **copie à l'instant T**, avec `origin_item` pour la traçabilité — **pas un lien
vivant**. Possible même depuis un round encore ouvert. Copie et non référence, pour que le
facilitateur puisse reformuler un sujet sans réécrire l'historique de l'activité source.

## Temps réel (cible)

- Le WebSocket **diffuse uniquement**. Toutes les écritures passent par l'API REST, avec la
  validation et les permissions déjà en place. ⚠️ **Écart majeur avec l'existant** — voir §Écarts.
- Events publics : `activity_changed`, `state_changed`, `progress`, `revealed`,
  `participant.joined/left`, `item.added/updated/removed`.
- Events facilitateur seul : `responses` (si `facilitator_live_view`, défaut false),
  `pending`. **Filtrage à l'émission, jamais côté client.**
- Mode live : agrégats regroupés par fenêtre de ~300 ms.
- Reconnexion : renvoi du token participant → snapshot complet. **Pas de rejeu d'events.**
  *(Déjà vrai : `build_state_sync`.)*
- Timer : serveur autoritaire, décompte client cosmétique. **Le reveal reste manuel.**
  ⚠️ L'existant `reveal_on_timeout` révèle automatiquement — écart connu, à traiter quand
  CLOSED sera introduit.
- Mode projection : client WebSocket autonome, URL propre, reçoit les events publics,
  n'envoie rien.

## Rôles (cible)

- `Room.owner` (master) — transfert possible, définitif. L'historique déjà produit reste
  consultable par l'ancien owner.
- Délégué : pilote, ne peut ni déléguer, ni transférer, ni supprimer.
- Permissions `can_facilitate` (owner + délégués + membres facilitateurs du Team) et
  `can_administer` (owner seul).
- **Un seul facilitateur actif à la fois**, la main se prend explicitement. L'owner peut la
  reprendre sans accord.
- Les participants ne sont **jamais** des membres : token + nom, aucun compte. Le compte
  n'est requis que côté facilitateur, et **les rooms anonymes restent le défaut**.

## Frontend (cible, dépôt `Facilitation_frontend`)

- `/room/:code` reste une **route unique** : le participant ne navigue jamais, l'écran suit
  l'état du socket. Ajouter `/room/:code/screen` pour la projection.
- Découpage cible de `room.component` (aujourd'hui 659 lignes TS + 305 HTML + 938 SCSS) :
  un **shell de room** (identité, socket, thème, QR, participants, timer), un **composant
  d'activité** résolu par le registre et chargé dynamiquement, et pour chaque activité
  **deux composants distincts** — facilitateur et participant. Pas un composant unique
  piloté par des `computed` de rôle.
- `RoomSocketService` détient l'état : le scinder en état de room (générique) et état
  d'activité (par type), détruit à chaque bascule.
- Mobile-first côté participant, desktop côté facilitateur.
- Aucun drag & drop n'existe aujourd'hui. Avant de figer PrimeNG pour Weighted Ranking,
  **prototyper sur téléphone** : le DnD PrimeNG repose sur l'API HTML5, dont le support
  tactile est faible. Le CDK Angular cohabite sans problème. Repli acceptable : boutons
  haut/bas.

---

## Plan, dans l'ordre

1. ~~**Renommer `VoteSession` → `Round`**, migrations comprises.~~ ✅ **fait** — migration
   `0009_votesession_to_round`, 240 passed. Côté back uniquement : le contrat WS est
   inchangé, donc `Facilitation_frontend` n'a rien à reprendre.
2. **Étoffer l'e2e front** — il n'y a qu'un seul spec, `vote-cycle.spec.ts`. C'est le filet
   qui protège le poker pendant l'extraction.
3. **Extraire le poker** de `room.component` en première activité. Rendu identique, donc
   vérifiable à l'œil.
4. **Registre d'activités**, back et front.
5. **N items par round**, un programme en cinq livraisons **5a → 5e**, détaillé dans
   `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` (conception, §8 marque
   l'avancement) et `docs/superpowers/plans/2026-09-11-5a-items-du-round.md` /
   `.superpowers/sdd/2026-09-11-5b-responses/` (plan et exécution). **5a et 5b sont faites** :
   - **5a** : `Round.items` (migrations 0010-0012), les intentions `item.*` + `round.select`
     + `round.add`, `items`/`round` dans `state.sync` (contrat §8.1).
   - **5b** : `Vote` → `Response` (migrations 0013-0016), `payload` JSON par item, contrainte
     d'unicité `(item, participant)`, `response.cast`, agrégation par item déportée dans
     `realtime/activities.py` (registre d'activités). Les alias hérités (`subject.*`,
     `vote.cast`, `myVote`, clés plates `tally`/`spread`/`votes`) ont tous été retirés une fois
     la bascule de `Facilitation_frontend` vérifiée en production (contrat §8.1.b/§8.2.b).
   - **5c** : `Round.config` (JSONField, défaut `{}`), deck figé **sur le round** dès
     `round.prepare` (et non plus seulement à l'ouverture, ce qui évite qu'un second round
     préparé avec un autre deck réécrive celui du premier), validation de la configuration
     par le registre (`activities.validate_config` / `config_schema`), intention
     `round.configure` (contrat §8.3). **Pas de `Round.vote_type`** — décision actée en
     cours de livraison, voir `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md`
     §3 et l'écart ci-dessous. Appris au passage : un round garde son type (son
     `deck_snapshot`) pour toute sa vie — `vote.reset` ne l'efface plus, et rejouer un round
     acté le recopie avec `config` et les items.
   - **5d** : `Round.sequence` (ordre explicite, sans trou quand un round est
     retiré), `round.reorder` / `round.remove` câblés sur le contrat WS, agenda
     enrichi de `state` et `everDecided`, écran de préparation front
     (réordonnancement, élagage). Pas de nouveau message `scenario.*` ni
     d'états `DRAFT`/`READY` — écarts assumés, voir
     `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` §8.
   - **5e** : `Round.source_round` / `source_rule` (la liaison de chaînage,
     `auto` et `manual`), `Round.source_resolved_at` (marqueur d'idempotence
     dédié — voir l'écart appris ci-dessous) et `Item.source_item` (parent
     direct d'une copie, distinct d'`origin_item` qui remonte à la racine),
     résolution en copie (`realtime/services.py::bind_round`/
     `resolve_source`), garde-fous du registre (`ActivitySpec.rank_value`),
     `round.bind` / `round.resolve` au contrat (§8.5), écran de sélection
     manuelle côté front. Vérifié en production le 2026-09-12 (salle 3WGR6E).
     **Le programme 5a → 5e est clos** — voir
     `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` §8 pour
     ce que les cinq livraisons ont produit ensemble, et pour deux écarts
     appris pendant 5e que ce document ne prévoyait pas.
6. **Dot Voting** — prochaine étape, première activité neuve. Choisie avant Weighted
   Ranking parce qu'elle exerce le modèle N-items sans le risque du drag & drop
   tactile, et parce qu'elle sera la première à exercer le chaînage de 5e entre
   **deux types** d'activité différents (le poker n'a chaîné qu'avec lui-même).

MVP visé ensuite : Planning Poker, Delegation Poker, Roman Vote, Dot Voting,
Weighted Ranking, QCM/Poll, ROTI.

## Règles de travail

- **`pytest` vert à chaque commit.** Référence actuelle : 381 passed.
- Le poker existant doit continuer à fonctionner **à chaque étape**. Aucune étape ne livre
  une régression « qu'on corrigera après ».
- Étapes petites et testables. Pas de réécriture de masse.
- Ne pas toucher au contenu produit sans décision explicite (voir §Vocabulaire).
- Voir `FORK.md` pour ce qui a été renommé au fork et ce qui reste à faire côté infra.

---

## Écarts connus entre la cible et le code

À vérifier avant de citer une ligne « Cible » comme un fait :

- **Le WS écrit aujourd'hui.** `RoomConsumer` reçoit des intentions (`response.cast`,
  `item.add`, `round.prepare`…) et écrit en base via `services`. La cible « le WS diffuse
  uniquement, toutes les écritures passent par REST » est une **refonte à faire**, pas une
  description. L'API REST actuelle se limite à créer / rejoindre une room.
- **DRF uniquement.** Aucune trace de Django Ninja dans le dépôt ; ne pas l'introduire sans
  décision explicite.
- **`Room.owner`, `can_facilitate`, `can_administer`, `facilitator_live_view`
  n'existent pas.** Seul `Team.owner` existe. L'autorité en room passe aujourd'hui par
  `rooms.Role.FACILITATOR` + `_require_facilitator`.
- **Le chaînage inter-activités n'est plus un écart — il est livré (5e).**
  `Round.source_round` / `source_rule` déclarent la liaison, `resolve_source`
  la résout en copie, `Item.origin_item` (racine de la chaîne) et
  `Item.source_item` (parent direct) portent la traçabilité. Seule limite
  réelle aujourd'hui : le registre ne compte qu'une seule activité
  (`delegation_poker`), donc le chaînage n'a encore été exercé qu'entre deux
  rounds de la **même** activité — Dot Voting (étape 6) sera le premier à le
  faire jouer entre deux **types** différents.
- **`reveal_on_timeout` révèle automatiquement**, alors que la cible veut un reveal manuel.
- **CLOSED n'existe pas** dans `RoundState`.
- **`Round.vote_type` n'existe pas — et n'existera pas.** Ce n'est pas la même chose qu'un
  champ qui manque encore (comme `Room.owner` ci-dessus) : c'est une décision actée en 5c,
  pas un oubli. Le type d'un round vit dans `Round.deck_snapshot` (`voteType` +
  `resolutionStrategy`), déjà figé sur le round ; une FK aurait dupliqué cette information
  dans les tables `decks`, que la couche temps réel n'a pas le droit de lire (règle
  d'immuabilité). Voir `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` §3.

## Pièges

- **Capacité de la box : `t3.medium` (4 Go) depuis le 2026-09-10. Celery est actif.**
  La machine était en `t3.small` (1,9 Go) pour **dix** applications Django. Le démarrage de
  `facilitation-celery` + `facilitation-celery-beat` a suffi à la faire basculer : swap plein
  (2047/2047), load à **76**, **toute la flotte injoignable** — `poker-api` répondait en 39 s,
  les autres en timeout. Seul `netdata` restait vif, n'ayant pas de backend Django : c'est ce
  qui a montré que nginx tenait et que la box n'était pas morte, mais saturée.

  Après redimensionnement : Facilitation consomme **375 Mo** (asgi 112, celery 162, beat 101)
  avec **~660 Mo disponibles** et un swap quasi nul. Les mêmes 375 Mo devaient auparavant
  tenir dans 113 Mo de marge.

  **Le mécanisme de protection reste en place, et c'est voulu.** Activer Celery est une
  décision explicite, prise hors bande, que le déploiement ne peut ni imposer ni annuler :

  1. `.github/workflows/deploy.yml` n'appelle `systemctl enable` que sur
     `facilitation-env-fetch` et `facilitation-asgi`. Il les activait toutes les quatre, ce
     qui **réactivait Celery à chaque déploiement** — corriger `deploy.sh` seul n'avait donc
     servi à rien. Les units restent installées par le `for u in …` juste au-dessus.
  2. `deploy/deploy.sh` ne redémarre Celery que si l'unit est `enabled`. Celery l'étant
     désormais, il est bien redémarré à chaque déploiement.

  Pour le désactiver de nouveau :
  `sudo systemctl disable --now facilitation-celery facilitation-celery-beat`.

  Corollaire pour la flotte : ajouter un site à cette machine n'est pas une opération neutre.
  Vérifier `free -m` **avant**, pas après.

- **Les attentes fixes dans les tests async sont calibrées sur SQLite et mentent sur
  PostgreSQL.** `test_timer_resumes_on_reconnect_after_restart` échouait sur PostgreSQL
  (vert sur SQLite) depuis le fork — **corrigé**, mais l'enseignement vaut pour tout nouveau
  test du consumer.

  Mesuré par instrumentation horodatée : le tail de `_handle_join` (`_reconcile_timeout` +
  `_resume_timeout`) prend **~9 ms sur SQLite** et **~256 ms sur PostgreSQL**, où le thread du
  pool doit ouvrir une connexion. Le test accordait un `asyncio.sleep(0.1)` fixe avant
  d'inspecter `_timer_tasks` : sur PostgreSQL il regardait trop tôt et voyait un dict vide.
  **Le code de production n'était pas en cause** — la reprise du timer fonctionne.

  Deux pièges de diagnostic rencontrés, à ne pas refaire : le `CancelledError` de la trace est
  un **leurre** (il survient ~310 ms *après* l'assertion, au teardown), et allonger simplement
  l'attente déplace l'échec au lieu de le corriger, car la deadline de 400 ms expirait alors
  pendant l'attente.

  Correctif appliqué : `_wait_for_timer_task()` sonde la **condition** au lieu d'attendre une
  durée, la deadline passe à 2,5 s pour contenir le tail, et `_drain_until()` accepte un
  `timeout` (le défaut de `WebsocketCommunicator` est 1 s). Vérifié par mutation — en
  neutralisant `_resume_timeout`, le test échoue toujours.

  **Le second cas était pire, et il est corrigé.**
  `test_reconnect_does_not_duplicate_tracked_timer_task` vérifie une **absence** de
  changement : inspecter trop tôt le faisait *passer* sans rien vérifier. Démontré par
  mutation sur PostgreSQL — en retirant la garde `if code not in _timer_tasks` de
  `_resume_timeout`, l'ancienne version passait quand même, la nouvelle échoue.

  **La bonne barrière est un aller-retour, pas une attente** : le consumer traite les
  messages d'une même connexion **en série**, donc recevoir un `pong` prouve que
  `_handle_join` est entièrement terminé, tail compris. Préférer ce motif à tout
  `asyncio.sleep()` dans un nouveau test du consumer :

  ```python
  await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
  await _drain_until(comm, "pong")
  ```

  **Complément appris pendant 5d (tâche 3) : cette barrière n'ordonne que la
  connexion qui l'émet.** `_handle_join` rend la main à l'appelant `ping`/`pong`
  dès que le `state.sync` de CETTE connexion est émis — ses diffusions aux
  voisins (`participant.joined`, `facilitator.presence`, …) peuvent encore être
  en vol sur le channel layer. Un `ping` envoyé depuis la connexion A ne prouve
  donc rien sur ce que la connexion B, encore en train de joindre, a déjà
  diffusé ou reçu : A et B sont deux boucles `receive_json` indépendantes, et
  rien n'ordonne le traitement du `ping` de A par rapport à l'achèvement du
  `_handle_join` de B. Rencontré sur un test flaky qui flushait le bruit de
  connexion d'une connexion voisine via un ping envoyé par le facilitateur
  (`realtime/tests/test_scenario_ws.py`, voir
  `.superpowers/sdd/2026-09-12-5d-scenario-prepare/task-3-report.md`) :
  intermittent, `facilitator.presence` apparaissant parfois dans la collecte
  finale au lieu d'avoir été flushé. **Le remède est de faire flusher chaque
  connexion par elle-même** — un `ping`/`pong` propre à CHAQUE connexion juste
  après son propre `session.join`, jamais un seul ping partagé pour garantir
  l'état de plusieurs connexions à la fois.

  **Second complément, appris en 6a tâche 5 : sur la connexion qui DÉCLENCHE
  elle-même l'action, un seul aller-retour ne suffit pas non plus.** Prouver
  qu'une connexion ne reçoit PAS une diffusion qu'elle vient elle-même de
  provoquer (ex. un votant qui émet `response.cast` et ne doit voir aucun
  `response.totals` en mode secret) est un cas distinct du complément
  ci-dessus (A qui observe B) : ici A observe ce que A lui-même a déclenché.
  Un seul `ping`/`pong` sur A a laissé passer une mutation qui aurait dû le
  faire échouer — `response.totals` apparaissait bien, mais **au tour
  suivant**, après le `pong`.

  Cause, dans `channels/consumer.py` (`await_many_dispatch`) : chaque
  connexion fait courir DEUX tâches concurrentes — `receive` (les messages
  client, dont le `ping`) et `channel_receive` (les diffusions de groupe,
  dont celle que `response.cast` vient de provoquer sur SA PROPRE
  connexion). Quand les deux sont prêtes en même temps, elles sont
  départagées dans l'ordre de la liste `[receive, self.channel_receive]` :
  le `ping`, déjà en file au moment du `response.cast`, est dispatché AVANT
  la diffusion que ce `response.cast` vient lui-même de déclencher, qui
  n'atteint donc le même correspondant qu'au tour suivant.

  Établi empiriquement (`realtime/tests/test_dot_voting_live.py`,
  `_ping_pong_types`) : neutraliser le gate de configuration testé ne faisait
  PAS échouer le test avec un seul `ping`/`pong` ; le remède, deux tours
  immédiats (deux `ping`/`pong` de suite sur la même connexion, tous les
  types collectés), a fait échouer le test comme attendu sur la même
  mutation. **Le remède : deux allers-retours, pas un**, quand la connexion
  qui vérifie une absence est celle qui a déclenché l'action :

  ```python
  for _ in range(2):
      await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
      await _drain_until(comm, "pong")  # collecter les types vus en chemin
  ```

- **Coordonnées d'infrastructure, relevées sur la box le 2026-09-10.** Port **8009**
  (`8000`–`8008` tous occupés, dont `8006` daphne Poker, `8007` gunicorn billing, `8008` daphne
  Fabric ; suivant occupé : `8125` netdata). Redis **db5** (`db0`–`db4` pris — un index partagé
  mélangerait les channel layers de deux applications). Base et rôle SQL `facilitation`, SSM
  `/facilitation/prod`, env `/run/facilitation/.env`, arbre
  `/var/www/django_websites/Facilitation_server`, rôle IAM `facilitation-deploy`.
  **Ne pas réutiliser une valeur de Poker :** jusqu'au 2026-09-10, `deploy/` et `deploy.yml`
  pointaient encore sur `Poker_server` / `/run/poker` / `/poker/prod` — un déploiement aurait
  visé l'installation Poker vivante.
- **Le site est en production depuis le 2026-09-10.** `https://facilitation-api.foxugly.com/health/`
  répond `{"status": "ok", "database": "ok"}`, les quatre units tournent, le WebSocket
  négocie bien un `101 Switching Protocols`. Séparation d'avec Poker vérifiée sur les cinq
  axes : bases distinctes (`facilitation` porte `rooms_round`, `poker` garde
  `rooms_votesession`), Redis `db5` vs `db3`, chemins des units, `/run` séparés, zéro
  croisement de processus.
- **Le claim OIDC de GitHub est au format *immuable*.** Le rôle `facilitation-deploy` doit
  accepter `repo:Foxugly@3275928/Facilitation_server@1363704826:environment:production` —
  le dépôt y est identifié par ses IDs numériques, pas par son nom. La forme classique
  `repo:Foxugly/Facilitation_server:...` **seule ne suffit pas** : `AssumeRoleWithWebIdentity`
  est refusé sans explication utile. Les deux formes sont déclarées, en `StringEquals` et
  sans joker. Même piège pour tout nouveau dépôt de la flotte.
- **Valider les migrations sur PostgreSQL.** Le dev local est en sqlite ; les violations
  NOT NULL / unique que sqlite laisse passer casseront en prod. La CI teste bien sur Postgres
  (délibérément) — faire confiance à la CI plutôt qu'à un pytest local vert.
- **Une migration qui écrit des données ET modifie le schéma dans la MÊME transaction peut
  passer sur sqlite et casser sur PostgreSQL.** Rencontré pendant 5b :
  `0014_votes_to_responses` transvasait les votes en réponses via `RunPython`, et la migration
  suivante voulait faire basculer la contrainte d'unicité de `Response` de
  `(round, participant)` vers `(item, participant)` dans la foulée. La CI a échoué sur
  PostgreSQL avec `cannot ALTER TABLE "rooms_response" because it has pending trigger events` —
  sqlite ne connaît pas cette contrainte, donc la suite locale restait verte pendant que la CI
  (PostgreSQL 16) rejetait le push. Correctif : scinder en deux migrations, chacune dans sa
  propre transaction — `0014` (RunPython, données) se valide avant que `0015`
  (`response_item_participant`, schéma) n'ouvre la sienne. Réflexe pour toute prochaine
  migration de données suivie d'un changement de contrainte/colonne sur la même table : les
  séparer par défaut, sqlite ne préviendra pas.
- **Retirer un alias du contrat WS suppose de vérifier, DANS LE DÉPÔT `Facilitation_frontend`,
  qu'il n'est plus émis — pas seulement de l'avoir prévu dans un plan.** Le plan 5b prévoyait
  de retirer `subject.set`/`subject.add`/`subject.select` en même temps que `vote.cast`
  (commit `9dc26ab`). Retrait annulé en urgence dans la foulée (`e4cc1e9`) une fois constaté
  que `Facilitation_frontend` (`room-socket.service.ts`) émettait toujours les trois — la
  bascule du front sur `item.*`/`round.select` planifiée en 5a n'avait en réalité jamais eu
  lieu, contrairement à ce que le plan supposait. Sans cette vérification, le déploiement
  aurait cassé la pose de sujet, l'ajout à la file et la sélection d'agenda **en production**.
  Le retrait effectif n'a eu lieu que trois déploiements plus tard (`4216152`), après une
  relecture confirmant que le front déployé n'émettait plus que la forme moderne. Réflexe :
  un plan qui affirme « le front a basculé » n'est une preuve de rien — grep (ou lire en prod)
  le dépôt frontend réellement déployé avant de retirer un alias.
- Index Redis : la prod utilise `redis://127.0.0.1:6379/3` sur une box partagée.
- `ROOM_MAX_PARTICIPANTS` (15) est une limite de **lisibilité** — les cartes tombent à ~61px
  à 15 sièges autour de l'ovale. Figée sur `Room.max_participants` à la création, tout comme
  `result_layout` : une room vivante ne change pas de forme sous les joueurs. À distinguer de
  `TEAM_MAX_MEMBERS` (20).
- Les rooms anonymes gratuites expirent après `ROOM_INACTIVITY_HOURS` (8h) via la tâche beat
  `rooms.tasks.expire_stale_rooms` ; **les rooms d'équipe n'expirent jamais** (`Room.is_live`).
- Les origines WS sont validées contre `CORS_ALLOWED_ORIGINS` **plus** `ALLOWED_HOSTS`
  (`config/asgi.py::_ws_allowed_origins`) : l'hôte du SPA diffère de celui de l'API, valider
  sur `ALLOWED_HOSTS` seul rejetterait tous les clients réels.
- Dependabot **ignore toutes les majeures** (npm/pip/gradle) par politique de flotte ; les
  correctifs de sécurité passent quand même, majeures comprises.
- **Le caractère `§` se fait remplacer en silence par certains outils d'écriture —
  vérifier après coup, pas avant.** Rencontré trois fois pendant 5e/5d : des
  citations de section du contrat ou de la conception transcrites `section7`,
  `sec8.5`, `SS8.4` au lieu du `§` littéral attendu partout ailleurs dans le
  dépôt. Le défaut ne casse rien à l'exécution — ce sont des commentaires ou de
  la doc — mais il rend la ligne **invisible à la recherche** qui sert
  justement à retrouver ce qui cite une section avant de la modifier (`grep
  '§8.5'` ne trouve pas `SS8.5`). Aucun outil ne prévient : la substitution
  passe pour un caractère normal tant qu'on ne la cherche pas. Remède : après
  avoir écrit un commentaire ou une doc qui cite une section, `grep -n "§"` (ou
  le numéro visé) sur le fichier modifié pour confirmer que le caractère est
  bien littéral — ne jamais supposer qu'un outil l'a transcrit correctement.

## Déploiement

Push sur `main` → GitHub Actions (`.github/workflows/deploy.yml`) joue pytest **sur
PostgreSQL 16** (délibérément, pas sqlite — sqlite est assez permissif pour masquer des
endpoints cassés), puis OIDC → SSM. Root installe les units systemd / le vhost nginx / le
script env-fetch **depuis le blob git committé**, puis lance `deploy/deploy.sh` en tant que
`django`. Ne jamais `cp` un artefact chargé par root depuis l'arbre inscriptible par django
(escalade de privilèges, OPERATIONS.md §3.10/§3.11).

Quatre units : `facilitation-env-fetch` (oneshot, SSM → `/run/facilitation/.env` en tmpfs),
`facilitation-asgi` (daphne), `facilitation-celery`, `facilitation-celery-beat`. Un déploiement
de code **ne redémarre pas** `env-fetch` : une valeur SSM modifiée exige un restart explicite.

Secrets : AWS SSM Parameter Store uniquement. Ne jamais committer de `.env` — `.gitignore`
couvre aussi `.env.*`, parce qu'un `.env.e2e-backup` porteur de vrais identifiants a déjà été
trouvé non ignoré sur un dépôt voisin.

## Specs

`docs/superpowers/specs/`, conservées telles quelles depuis Poker :

- `README-handoff-claude-code.md` — index + digest des conventions de flotte qui mordent. À lire en premier.
- `delegation-poker-realtime-contract.md` — protocole WS : enveloppe, deux sens, `state.sync`,
  autorité, cas limites, erreurs. Cité par numéro de section dans tout `realtime/`.
- `2026-07-08-data-model.md` — modèle de données détaillé, principes P1–P5, gotchas Postgres.
- `2026-07-09-phase2-design.md` et les `plans/` datés — décisions de conception, fonctionnalité par fonctionnalité.

Les commentaires du code citent ces documents par section (`contract §6.f`, `spec §5.4`,
`§3.16`) : en changeant un comportement, mettre à jour ou contester la spec plutôt que de
diverger en silence.
