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
| `Item` | un sujet manipulé par une activité. | **à renommer depuis `Subject`** |
| `Response` | la contribution d'un participant à un round. | **à renommer depuis `Vote`** |

**Le mot « Session » est banni du domaine.** Il entrait en collision frontale avec l'ancien
`VoteSession`. Ne jamais l'introduire, même en commentaire. Le code en est désormais purgé
(migration `0009_votesession_to_round`) à **une exception près, volontaire** : le type de
message WebSocket `session.join`, qui appartient au contrat (§4) et dont le renommage
casserait `Facilitation_frontend`. Le renommer suppose de livrer les deux dépôts ensemble.

**Convention :** les attributs de modèle s'appellent `round` (`Vote.round`, `Result.round`,
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
.\.venv\Scripts\python.exe -m pytest                              # suite complète — référence : 240 passed
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

## Trois couches dans la brique temps réel

1. `realtime/consumers.py` — `RoomConsumer`, mince. Valide l'enveloppe versionnée
   (`v == PROTOCOL_VERSION`, `type`, `payload`, `cid`), dispatche, et **rediffuse le fait**.
   Chaque appel domaine est enrobé dans `database_sync_to_async`. Les tâches de révélation à
   échéance vivent dans un dict `_timer_tasks` **au niveau du module**, pas sur l'instance :
   elles doivent survivre à la déconnexion du client qui a ouvert le vote.
2. `realtime/services.py` (675 lignes, le vrai cœur) — logique de domaine **synchrone et
   pauvre en framework** : machine à états, autorité, décomptes, `build_state_sync`. Gardée
   sync pour être testable sans socket. Un coup illégal lève
   `RoomError(code, message, rejected_type)` au lieu de s'appliquer.
3. `rooms/models.py` — persistance.

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
`ORDINAL_RESOLUTION_STRATEGIES` dans `services.py` conditionne l'écart min/max — un deck non
ordinal (vote romain, fist-of-five) renvoie `{min: None, max: None}` au lieu de calculer un
« 0 – 0 » de faux consensus à partir des seules valeurs qui passent `isdigit()`.

C'est ce registre embryonnaire qui rend la cible atteignable : **Planning Poker sera un
`VoteType` et un deck Fibonacci, pas du nouveau code.**

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
5. **N items par round** : `Round.subject` (FK unique) → jeu d'items, `Response.card_value`
   (CharField) → payload JSON. **Additif** : le poker garde ses champs actuels.
6. **Dot Voting** — première activité neuve. Choisie avant Weighted Ranking parce qu'elle
   exerce le modèle N-items sans le risque du drag & drop tactile.

MVP visé ensuite : Planning Poker, Delegation Poker, Roman Vote, Dot Voting,
Weighted Ranking, QCM/Poll, ROTI.

## Règles de travail

- **`pytest` vert à chaque commit.** Référence actuelle : 240 passed.
- Le poker existant doit continuer à fonctionner **à chaque étape**. Aucune étape ne livre
  une régression « qu'on corrigera après ».
- Étapes petites et testables. Pas de réécriture de masse.
- Ne pas toucher au contenu produit sans décision explicite (voir §Vocabulaire).
- Voir `FORK.md` pour ce qui a été renommé au fork et ce qui reste à faire côté infra.

---

## Écarts connus entre la cible et le code

À vérifier avant de citer une ligne « Cible » comme un fait :

- **Le WS écrit aujourd'hui.** `RoomConsumer` reçoit des intentions (`vote.cast`,
  `subject.set`, `round.prepare`…) et écrit en base via `services`. La cible « le WS diffuse
  uniquement, toutes les écritures passent par REST » est une **refonte à faire**, pas une
  description. L'API REST actuelle se limite à créer / rejoindre une room.
- **DRF uniquement.** Aucune trace de Django Ninja dans le dépôt ; ne pas l'introduire sans
  décision explicite.
- **`Room.owner`, `can_facilitate`, `can_administer`, `facilitator_live_view`, `origin_item`
  n'existent pas.** Seul `Team.owner` existe. L'autorité en room passe aujourd'hui par
  `rooms.Role.FACILITATOR` + `_require_facilitator`.
- **`reveal_on_timeout` révèle automatiquement**, alors que la cible veut un reveal manuel.
- **CLOSED n'existe pas** dans `RoundState`.

## Pièges

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
