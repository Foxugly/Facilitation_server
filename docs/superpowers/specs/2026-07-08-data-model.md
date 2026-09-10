# Delegation Poker — Modèle de données détaillé (livrable n°0)

**Date :** 2026-07-08
**Repo :** `Foxugly/Poker_server` (Django + DRF + Channels)
**Cible SGBD :** PostgreSQL 16 (prod, box-local, convention `DB_*` — OPERATIONS.md §3.13).
sqlite en dev uniquement. **Valider toute migration sur Postgres** (les tests sqlite passent là
où NOT NULL / unique Postgres échouent — §3.16, memory `fleet-django-migration-postgres-gotchas`).

**Statut :** ce document est le **préalable au code** exigé par le handoff (README §5, livrable n°0).
Il fige tables, champs, types, relations, clés, index, contraintes, la mécanique du **snapshot**,
la **machine à états** de la session, l'**expiration**, le **token participant** et la frontière
**Phase 1 / Phase 2**. Il consomme : `delegation-poker-scope.md` (§6 esquisse), `-design-phase1.md`,
`-realtime-contract.md`.

---

## 0. Principes de modélisation (rappel, actés)

| # | Principe | Conséquence sur le schéma |
|---|----------|---------------------------|
| P1 | **La DB décrit un type de vote ; le code décide du comportement.** | `VoteType.resolution_strategy` = identifiant routé côté code, pas de logique en DB. |
| P2 | **Langue = donnée**, `{ code_langue → texte }`, jamais de colonne `label_fr`. | Traductions via **django-parler** (`TranslatedFields`), une ligne par langue. |
| P3 | **Snapshot immuable du deck** dans la session (scope §3.6). | Le référentiel (Deck/Card/TextLayer) est *éditable* ; à la création de salle il est **gelé** en JSON (`deck_snapshot`). Le temps réel ne lit **jamais** le référentiel vivant. |
| P4 | **Secret réel des votes** (contrat §0.4). | `Vote.card_value` n'est jamais diffusé avant `revealed` (règle applicative, pas de contrainte DB). |
| P5 | **Rôle porté par le token, pas la connexion** (contrat §0.3). | `Participant.token` = secret ; `role` sur le participant ; la reconnexion résout token → participant → rôle + vote. |
| P6 | **email-only, PAS de `username`** (§3.16). | `accounts.User` (`USERNAME_FIELD="email"`) créé **dès la Phase 1** ; le « username » des participants est `Participant.display_name`, **champ d'affichage éphémère non authentifiant**. |

---

## 1. Décision d'architecture : `AUTH_USER_MODEL` dès la Phase 1

⚠️ **On crée l'app `accounts` avec le `CustomUser` email-only immédiatement**, même si aucune
fonctionnalité d'auth n'est livrée en Phase 1.

**Pourquoi.** Changer `AUTH_USER_MODEL` en cours de projet déclenche
`InconsistentMigrationHistory` (admin.0001 dépend de accounts.__first__) — la flotte l'a payé sur
foxugly avec un runbook SQL one-shot (§3.16). Poser `AUTH_USER_MODEL = "accounts.User"` sur une
base **vierge** est gratuit : la migration `accounts.0001` tourne simplement. On ne recommencera pas
l'erreur.

**Forme** (copie du pattern flotte §3.16, option (a) — pas de package partagé) :
- `accounts.User(AbstractBaseUser, PermissionsMixin)` : `email = EmailField(unique=True)` non-null
  non-blank, `USERNAME_FIELD = "email"`, `REQUIRED_FIELDS = []`, `email_confirmed = BooleanField`,
  `first_name`/`last_name`, `is_active`/`is_staff`, `date_joined`. **Aucun champ `username`.**
- `UserManager` custom (`create_user`/`create_superuser` sur l'email).
- `UserAdmin` custom (email-based `list_display`/`search_fields`/`ordering`/`fieldsets`).

En Phase 1 le modèle existe mais n'est **peuplé que par `createsuperuser`** (accès admin Django pour
saisir les decks). Les participants anonymes **ne sont pas** des `User`.

---

## 2. Vue d'ensemble (apps Django)

| App | Rôle | Livré en |
|-----|------|:--:|
| `accounts` | `User` email-only + admin (§1). | **Phase 1** (modèle), features auth Phase 2 |
| `decks` | Référentiel éditable : `VoteType`, `Deck`, `Card`, `TextLayer` (+ traductions parler). | **Phase 1** |
| `rooms` | Runtime : `Room`, `Participant`, `Subject`, `VoteSession`, `Vote`, `Result`. | **Phase 1** |
| `realtime` | Consumer Channels + logique WS (pas de modèle propre, ou modèles de présence légers). | **Phase 1** |
| `teams` *(esquisse)* | `Team`, `TeamMembership`, `HistoryEntry`, `DelegationBoard`. | **Phase 2 — non créé** |

> **Isolation ASGI/WS** (README §4) : la brique temps réel vit dans `realtime/` ; le reste du
> backend reste WSGI-compatible. Celery indépendant.

---

## 3. App `decks` — référentiel éditable (source des snapshots)

### 3.1 `VoteType`
Un type de vote abstrait (P1). En Phase 1, une seule ligne : `delegation_poker`.

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `code` | `SlugField(unique=True)` | ex. `delegation_poker`. Clé stable référencée dans le snapshot. |
| `resolution_strategy` | `CharField` | Identifiant routé **côté code** (P1) ; ex. `delegation_v1`. Non-null. |
| `is_active` | `BooleanField(default=True)` | |
| `created_at` | `DateTimeField(auto_now_add)` | |
| **parler** `name` | `CharField` (traduit) | Libellé affichable du type (5 langues). |

### 3.2 `Deck`
Un jeu de cartes rattaché à un `VoteType`. Phase 1 = un deck standard.

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `vote_type` | `FK(VoteType, PROTECT, related_name="decks")` | |
| `is_standard` | `BooleanField(default=True)` | Phase 1 = `True`. |
| `card_back_image` | `ImageField(upload_to=…)` | **Dos imposé** en gratuit (scope §4). |
| `team` | `FK("teams.Team", null=True, blank=True, SET_NULL)` | **Phase 2** (deck custom d'équipe). Nullable dès maintenant pour ne pas re-migrer. *La table `teams.Team` n'existe pas en Phase 1* → voir §7 (le FK est ajouté **avec** l'app teams en Phase 2 ; en Phase 1 le champ est **absent**). |
| `is_active` | `BooleanField(default=True)` | |
| `created_at` | `DateTimeField(auto_now_add)` | |
| **parler** `name` | `CharField` (traduit) | Nom du deck. |

> **Note FK `team`** : pour éviter une dépendance fantôme, `Deck.team` est **introduit en Phase 2**
> en même temps que l'app `teams` (une migration additive `AddField(null=True)`, sans risque). En
> Phase 1, `Deck` n'a pas ce champ. Documenté ici pour que le snapshot et l'admin restent stables.

### 3.3 `Card`
Une carte d'un deck. Phase 1 : 7 cartes (niveaux 1–7).

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `deck` | `FK(Deck, CASCADE, related_name="cards")` | |
| `value` | `CharField` | **Valeur canonique, langue-agnostique**, référencée par `Vote.card_value` et `Result.chosen_value`. Delegation Poker : `"1"`…`"7"` (le niveau). Sert aussi de défaut d'ordre. |
| `slug` | `SlugField` | Clé i18n stable (`tell`,`sell`,`consult`,`agree`,`advise`,`inquire`,`delegate`) → mappe `card.<slug>` côté front si besoin. |
| `order` | `PositiveSmallIntegerField` | Ordre d'affichage (1–7). |
| `background_image` | `ImageField(upload_to=…)` | Fond de carte (illustration maison). |
| `is_active` | `BooleanField(default=True)` | |
| **contrainte** | `UniqueConstraint(deck, value)` | Une valeur unique par deck. Aussi `UniqueConstraint(deck, order)`. |

> **Pas de calque « numéro » spécial** (scope §7) : le numéro est un `TextLayer` `static` ordinaire.
> **Pas de 8ᵉ carte « légende »** (scope, design §5) : c'est un texte d'aide hors deck.

### 3.4 `TextLayer` (`TranslatableModel`, parler)
N calques texte superposés sur une carte (overlay CSS/SVG, **pas de gravure serveur** — scope §8).

**Base (non traduit) :**

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `card` | `FK(Card, CASCADE, related_name="layers")` | |
| `order` | `PositiveSmallIntegerField` | Ordre de superposition (z). |
| `pos_x` | `DecimalField(max_digits=5, decimal_places=2)` | **% horizontal** 0–100. |
| `pos_y` | `DecimalField(max_digits=5, decimal_places=2)` | **% vertical** 0–100. |
| `font_family` | `CharField` | |
| `font_size` | `DecimalField` | En **% de la hauteur de carte** (responsive) — pas en px absolus. |
| `font_weight` | `PositiveSmallIntegerField(default=400)` | |
| `color` | `CharField(max_length=9)` | Hex `#RRGGBB[AA]`. |
| `align` | `CharField(choices=left/center/right, default=center)` | |
| `content_kind` | `CharField(choices=static/i18n, default=i18n)` | `static` = même texte partout (ex. le numéro) ; `i18n` = traduit. |

**Traduit (parler `TranslatedFields`) :**

| Champ | Type | Notes |
|-------|------|-------|
| `content` | `CharField` | Le texte, **une ligne par langue** (table `decks_textlayer_translation`, PK `(master, language_code)`). |

**Résolution du texte** (`layer.resolve(lang)`), portée par le **code** :
- `content_kind == "static"` → renvoyer la traduction de la **langue de repli** (EN) — une seule
  ligne suffit, parler la sert partout via fallback. (Le « single value » du scope = un layer dont
  seule la langue fallback est peuplée.)
- `content_kind == "i18n"` → traduction de `lang`, **fallback EN** si absente (config parler).

> Ainsi « valeur unique **ou** jeu de traductions » (scope §7) est modélisé **sans** colonne en dur :
> un layer statique = 1 ligne de traduction (fallback) ; un layer i18n = N lignes. Ajouter une langue
> = **insérer des lignes** (P2), zéro migration de schéma.

### 3.5 Langues — source unique
La liste des langues supportées vit dans **un seul tuple de settings**
(`LANGUAGES` → dérive `PARLER_LANGUAGES`), consulté par parler, la validation et le fallback
(scope §10, exigence extensibilité). Départ : `fr, nl, en, it, es`. **Fallback = `en`**
(`PARLER_LANGUAGES` `default: {fallbacks: ["en"], hide_untranslated: False}`).
Pas de table `Language` en Phase 1 (YAGNI) ; si une gestion runtime des langues devient nécessaire,
elle s'ajoute en Phase 2 sans toucher au reste (les traductions sont déjà des données).

---

## 4. Snapshot du deck (mécanique, P3)

À la **création d'une salle** (`POST /api/rooms`), le référentiel est **sérialisé et gelé** dans un
`JSONField` porté par la `Room` (`Room.deck_snapshot`). Le WS et le `state.sync` ne lisent que ce
blob → historique immuable même si l'admin édite le deck ensuite.

**Forme du snapshot** (= `deckSnapshot` du contrat HTTP §1 et de `state.sync` §5.1) :

```json
{
  "voteType": "delegation_poker",
  "resolutionStrategy": "delegation_v1",
  "deckId": 1,
  "cardBack": { "image": "/media/decks/back.webp" },
  "cards": [
    {
      "value": "1",
      "slug": "tell",
      "order": 1,
      "background": { "image": "/media/decks/1-tell.webp" },
      "layers": [
        { "kind": "static", "order": 1, "x": 12.0, "y": 12.0, "font": "Inter",
          "size": 9.0, "weight": 700, "color": "#ffffff", "align": "center", "text": "1" },
        { "kind": "i18n", "order": 2, "x": 50.0, "y": 82.0, "font": "Inter",
          "size": 7.0, "weight": 600, "color": "#ffffff", "align": "center",
          "text": { "fr": "Dire", "nl": "Vertellen", "en": "Tell", "it": "Dire", "es": "Decir" } }
      ]
    }
    // … 7 cartes
  ]
}
```

- `layers[].text` est **une chaîne** (kind `static`) ou un **objet `{lang: texte}`** (kind `i18n`).
  Le front choisit selon `content_kind` + la langue du participant (fallback EN).
- Le snapshot est **auto-suffisant** : aucune requête sur `decks` au runtime.
- **Phase 1 : snapshot au niveau `Room`** (un seul deck par salle pour toute sa vie). Le champ
  `VoteSession.deck_snapshot` (scope §6) est *dérivé* de la salle en Phase 1 ; il ne devient un
  snapshot **propre à la session** qu'en Phase 2 (votes pré-créés / decks variables). On documente
  la cible mais on ne duplique pas le blob par session en Phase 1.

---

## 5. App `rooms` — runtime (cœur Phase 1)

### 5.1 `Room`

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `code` | `CharField(max_length=8, unique=True)` | Code public **6–8 car.**, casse insensible (stocké **UPPER**), caractères ambigus **exclus** (O/0/o, I/l/1). Généré serveur, collisions gérées (regénérer sur conflit). `db_index` via unique. |
| `title` | `CharField(blank=True)` | Titre de salle (optionnel). ≠ sujet de vote. |
| `vote_type` | `FK(decks.VoteType, PROTECT)` | Type joué dans la salle. |
| `deck_snapshot` | `JSONField` | **Gelé** à la création (§4). Immuable. |
| `current_session` | `FK(VoteSession, null=True, SET_NULL, related_name="+")` | Le tour courant (pointeur de commodité). |
| `created_at` | `DateTimeField(auto_now_add)` | |
| `last_activity_at` | `DateTimeField(db_index=True)` | Mis à jour à **chaque** activité WS. Base de l'expiration. |
| `expires_at` | `DateTimeField` | = `last_activity_at + 8h`. Recalculé à chaque activité. Index pour le balayage. |
| `team` | `FK("teams.Team", null=True, SET_NULL)` | **Phase 2** (salle d'équipe). Absent en Phase 1 (ajout additif Phase 2). |

**Expiration (scope §4, 8 h d'inactivité) :**
- Toute intention WS traitée met à jour `last_activity_at = now()` et `expires_at = now()+8h`.
- **Balayage** : tâche **Celery beat** périodique (ex. toutes les 15 min) qui marque/supprime les
  salles `expires_at < now()` (soft-delete `is_expired` ou suppression + cascade). **Défense
  paresseuse** : `POST /join`, `GET /rooms/{code}` et `session.join` renvoient **404 / `room.expired`**
  si `expires_at < now()` même avant passage du balayeur (le beat n'est qu'un nettoyage de fond).
- Choix : `is_expired = BooleanField(default=False)` + `expires_at` (garder la ligne pour Phase 2
  historique éventuel ; en gratuit anonyme, une suppression cascade est acceptable — **décision :
  soft-flag en Phase 1**, purge physique différée).

### 5.2 `Participant`

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `room` | `FK(Room, CASCADE, related_name="participants")` | |
| `token` | `CharField(max_length=64, unique=True, db_index=True)` | **Secret aléatoire** (≥ 32 o base62), généré serveur (§P5). Rejoué à chaque (re)connexion WS. **Jamais** exposé aux autres clients. |
| `public_id` | `CharField / UUID` | `participantId` diffusé aux autres (contrat §5). **≠ token.** |
| `display_name` | `CharField(max_length=50)` | Le « username » **d'affichage éphémère** (P6). Non authentifiant. |
| `role` | `CharField(choices=facilitator/voter, default=voter)` | Le **créateur** est `facilitator` (contrat §3). |
| `is_connected` | `BooleanField(default=False)` | Présence (mise à jour connect/disconnect WS). |
| `last_seen_at` | `DateTimeField` | Heartbeat (contrat §8). Base du garde-fou facilitateur 60 s. |
| `user` | `FK("accounts.User", null=True, SET_NULL)` | **Phase 2** (membre authentifié). Nullable dès maintenant (le champ **peut** exister en Phase 1 puisque `accounts.User` existe — voir §1 ; on le pose dès Phase 1). |
| `created_at` | `DateTimeField(auto_now_add)` | |

**Contraintes :** `UniqueConstraint(room, public_id)`. Un `token` = **un** participant
(double-onglet même token → la nouvelle connexion remplace l'ancienne, contrat §6.g — géré au niveau
connexion, pas de doublon en base).

### 5.3 `Subject`

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `room` | `FK(Room, CASCADE, related_name="subjects")` | Une salle enchaîne plusieurs sujets. |
| `text` | `CharField` | La décision votée. |
| `sequence` | `PositiveSmallIntegerField` | Ordre dans la salle. |
| `dimension` | `CharField(choices=as_is/to_be, null=True)` | **Phase 2 board** (1 case cochée = 1 tour). Nullable/absent en Phase 1 (ajout additif). |
| `created_at` | `DateTimeField(auto_now_add)` | |

### 5.4 `VoteSession` (le « tour »)

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `room` | `FK(Room, CASCADE, related_name="sessions")` | |
| `subject` | `FK(Subject, PROTECT, related_name="sessions")` | Le sujet du tour. |
| `state` | `CharField(choices)` | **Machine à états** : `idle → open → revealed → acted` (design §4). Défaut `idle`. |
| `facilitator` | `FK(Participant, SET_NULL, null=True, related_name="+")` | Rôle de contrôle, **transférable** (garde-fou §6.f ; transfert volontaire = Phase 2). |
| `opened_at` | `DateTimeField(null=True)` | Passage `open`. |
| `revealed_at` | `DateTimeField(null=True)` | Passage `revealed`. |
| `created_at` | `DateTimeField(auto_now_add)` | |

**Machine à états (transitions autorisées, autorité facilitateur — contrat §4) :**

```
idle --vote.open (facilitator, sujet défini)--> open
open --vote.cast (votant, remplace)------------> open      (pas de transition, MAJ Vote)
open --vote.reveal (facilitator, >=1 vote)-----> revealed
revealed --result.act (facilitator)-----------> acted
revealed|acted --vote.reset (facilitator)------> idle | open   (nouveau tour)
```

Toute intention **incohérente** avec `state` est **rejetée** (`error state.invalid_transition`,
contrat §6.b), jamais appliquée. `vote.cast` n'est accepté qu'en `open`.

### 5.5 `Vote`

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `session` | `FK(VoteSession, CASCADE, related_name="votes")` | |
| `participant` | `FK(Participant, CASCADE, related_name="votes")` | |
| `card_value` | `CharField` | Doit correspondre à un `cards[].value` du **snapshot**. **Jamais diffusé avant `revealed`** (P4). |
| `created_at` / `updated_at` | `DateTimeField` | Modifiable **tant que `open`** (contrat §4). |
| **contrainte** | `UniqueConstraint(session, participant)` | Un seul vote par participant par tour ; re-voter = **remplacement** (upsert), même valeur = no-op idempotent. |

### 5.6 `Result` (résultat acté)

| Champ | Type | Notes |
|-------|------|-------|
| `id` | BigAuto | PK |
| `session` | `OneToOneField(VoteSession, CASCADE, related_name="result")` | Un résultat par tour acté. |
| `subject` | `FK(Subject, PROTECT, related_name="results")` | Redondance utile Phase 2 (board = agrégation par sujet). |
| `chosen_value` | `CharField` | Niveau retenu (∈ snapshot values). Défaut proposé = **mode/médiane** des votes (calcul code, modifiable par le facilitateur — design §4, contrat §4 `result.act`). |
| `decided_by` | `FK(Participant, SET_NULL, null=True)` | Facilitateur qui a acté. |
| `decided_at` | `DateTimeField(auto_now_add)` | Daté (Phase 2 historique). |

---

## 6. Correspondance contrat WS ↔ modèle (traçabilité)

| Élément contrat | Porté par |
|-----------------|-----------|
| `participantToken` (HTTP) | `Participant.token` |
| `role` | `Participant.role` |
| `deckSnapshot` | `Room.deck_snapshot` |
| `roundState` | `VoteSession.state` |
| `subject` | `Subject.text` (via `VoteSession.subject`) |
| `participation.update {voted,total,votedIds}` | dérivé de `Vote` du tour (IDs = `Participant.public_id`, **jamais** de valeurs) |
| `vote.revealed {votes,spread}` | `Vote.card_value` du tour (exposés **seulement** en `revealed`) + min/max |
| `myVote` (state.sync) | `Vote` du destinataire uniquement |
| `result` | `Result.chosen_value` |
| `facilitatorPresent` | `Participant(role=facilitator).is_connected` + `last_seen_at` (garde-fou 60 s) |
| `facilitator.changed` | réassignation `VoteSession.facilitator` + **nouveau `token`** émis (contrat §6.f) |

---

## 7. Frontière Phase 1 / Phase 2 (ce qui N'EST PAS créé maintenant)

**Non créé en Phase 1** (YAGNI, README §6) — app `teams` :
- `Team` (nom, membres, decks custom, historique, boards).
- `TeamMembership` (`user` ↔ `team`, rôle).
- `HistoryEntry` (session archivée, résultats actés, datée, rattachée équipe).
- `DelegationBoard` (**vue d'agrégation** sur les `Result` actés ; ligne = sujet ; AS-IS/TO-BE).

Ces tables arriveront avec la Phase 2. Les **points d'ancrage** posés dès maintenant pour éviter des
migrations douloureuses :
- `accounts.User` existe (§1) → `Participant.user` (nullable) posable en Phase 1.
- `Deck.team`, `Room.team`, `Subject.dimension` : champs **additifs** (`AddField null=True`) en
  Phase 2 — **non présents** en Phase 1 pour ne pas référencer une app inexistante.

**Hors périmètre modèle Phase 1 :** decks custom, dos personnalisable/upload, votes pré-créés,
transfert *volontaire* de facilitateur, board/export, email d'historique, Stripe.

---

## 8. Index, contraintes & gotchas Postgres

- **Unicité insensible à la casse du code salle** : `Room.code` stocké **UPPER** + `unique=True`
  (pas besoin d'index fonctionnel `lower()` puisqu'on normalise à l'écriture ; la recherche
  normalise l'entrée en UPPER). ⚠️ Gotcha flotte : un `db_index=True` **couplé** à `unique=True` sur
  Postgres crée un **double index `_like`** qui casse certaines migrations (memory
  `fleet-django-migration-postgres-gotchas`) → sur `code` on garde **`unique=True` seul**, pas de
  `db_index` redondant.
- `Participant.token` : `unique=True` (secret) — suffisant, pas de `db_index` en plus.
- `Vote` : `UniqueConstraint(session, participant)`.
- `Card` : `UniqueConstraint(deck, value)` + `UniqueConstraint(deck, order)`.
- `Room.last_activity_at` / `expires_at` : `db_index=True` (balayage d'expiration).
- **Valider les migrations sur Postgres**, pas seulement sqlite (NOT NULL / unique divergents).
- `accounts.User` : suivre exactement la forme §3.16 (manager custom, admin custom) — base vierge,
  aucune migration de dé-duplication d'email nécessaire (contrairement au reste de la flotte).

---

## 9. Ce que Claude Code doit produire ensuite (après validation de ce doc)

1. **Échafaudage backend** §3.12 : projet Django, settings `DB_*`, `/health/` (check DB), Sentry
   `poker-backend`, ASGI (Channels) + Redis, Celery. `accounts.User` + admin. CI/CD OIDC→SSM.
2. Migrations `decks` + `rooms` (validées **sur Postgres**), admin de saisie des 7 cartes + calques.
3. Sérialiseur de **snapshot** + endpoints HTTP salle (contrat §1).
4. Consumer Channels (contrat §4–§8) + machine à états (§5.4) + garde-fou facilitateur.
5. Tests (cycle de vote, secret des votes, reconnexion/`state.sync`, expiration) — **le temps réel
   d'abord** (zone à risque).

---

*Fin du modèle de données détaillé (livrable n°0).*
