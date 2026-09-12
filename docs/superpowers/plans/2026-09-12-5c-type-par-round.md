# Livraison 5c — le type d'activité se choisit par round

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qu'un scénario puisse enchaîner des activités différentes — Delegation Poker puis, demain, Dot Voting — en faisant du round, et non de la room, le porteur du type d'activité et de sa configuration.

**Architecture:** Le type est **déjà** figé par round : `Round.deck_snapshot` porte `voteType` et `resolutionStrategy`, et `_resolution_strategy` le lit en priorité sur celui de la room. Ce qui manque n'est donc pas un champ de plus, mais deux choses : le snapshot du round est figé **trop tard** (à l'ouverture, alors que le facilitateur choisit le deck à la préparation, ce qui écrit sur la room et déborde sur les autres rounds) ; et aucune **configuration** propre à l'activité n'existe. Cette livraison fige le deck du round dès sa préparation et ajoute `Round.config`, validé par le registre.

**Tech Stack:** Django 5 + DRF + Channels (daphne), PostgreSQL en CI/prod, sqlite en dev, pytest + pytest-django + pytest-asyncio. Front : Angular 21 (`Facilitation_frontend`), Playwright.

**Spec:** `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` (§3 modèle, §5 contrat, §6 registre, §8 découpage)

## La décision qui s'écarte du design

Le design annonçait `Round.vote_type` (clé étrangère vers `decks.VoteType`) **plus** `Round.config`. Le code montre que la clé étrangère serait une **seconde source de vérité** pour une information que `Round.deck_snapshot` porte déjà — et une source moins fiable, puisque la règle d'immuabilité du dépôt veut que la couche temps réel ne lise jamais les tables `decks`, seulement le blob figé. Deux sources finiraient par diverger : un round dont le `vote_type` dit une chose et le snapshot une autre est un bug qu'aucun test n'attraperait.

**Décision : pas de `Round.vote_type`.** Le type d'un round est celui de son `deck_snapshot`, point. Ce plan fige ce snapshot plus tôt, et ajoute la seule chose qui manque vraiment : `Round.config`. Le design §3 est à corriger en conséquence — c'est la tâche 5.

## Global Constraints

- **`pytest` vert à chaque commit.** Référence d'entrée : **284 passed**.
- Commandes : `.\.venv\Scripts\python.exe -m pytest -q`, `.\.venv\Scripts\python.exe manage.py <cmd>` (Windows, venv Python 3.14).
- **Le poker doit rester jouable après chaque tâche**, y compris depuis le front déployé en production.
- **Aucun message de commit accentué** ; commentaires de code en français sans accents.
- **Tests async du consumer : jamais d'`asyncio.sleep()`** pour attendre un fait — la barrière est un aller-retour `ping`/`pong`. `_drain_until` abandonne au bout de 8 messages.
- Le mot « Session » reste banni du domaine, sauf le message `session.join`.
- **La couche temps réel ne lit jamais les tables `decks`** : elle ne lit que les blobs figés (`Room.deck_snapshot`, `Room.deck_snapshots`, `Round.deck_snapshot`). Cette livraison ne doit pas introduire la première exception.
- **Valider sur PostgreSQL par la CI** avant tout déploiement ; la suite locale est en sqlite.
- Une migration ne mélange **jamais** écriture de données et `ALTER TABLE` : PostgreSQL refuse l'`ALTER TABLE` tant que des événements de trigger restent en attente. Deux migrations séparées le cas échéant.

---

### Task 1: `Round.config` et le deck figé dès la préparation

**Files:**
- Modify: `rooms/models.py` (`Round.config`)
- Create: `rooms/migrations/0017_round_config.py`
- Modify: `realtime/services.py` (`prepare_round`, `select_deck`, `open_vote`)
- Test: `realtime/tests/test_round_type.py`

**Interfaces:**
- Produces: `Round.config` (JSON, défaut `{}`) ; `services.prepare_round(..., deck_id=...)` fige désormais `rnd.deck_snapshot` **immédiatement** au lieu de laisser `open_vote` le faire.

- [ ] **Step 1: Écrire les tests**

Créer `realtime/tests/test_round_type.py`. Ils doivent démontrer, chacun par une valeur concrète :

1. Deux rounds préparés dans la **même room** avec deux decks différents gardent chacun le sien : le `deck_snapshot` du premier ne change pas quand le second est préparé. *(C'est l'objet de la livraison : sans lui, rien ne prouve qu'un scénario peut mélanger les activités.)*
2. Ouvrir un round n'écrase pas le snapshot figé à sa préparation.
3. Un round préparé sans choix explicite de deck hérite de celui de la room — le comportement d'aujourd'hui, qui ne doit pas changer pour le poker.
4. `Round.config` vaut `{}` par défaut et survit à un aller-retour en base.

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `.\.venv\Scripts\python.exe -m pytest realtime/tests/test_round_type.py -q`
Expected: FAIL — `Round` n'a pas d'attribut `config`, et les deux premiers tests échouent sur un snapshot partagé.

- [ ] **Step 3: Le champ**

```python
    # Configuration de l'activite jouee par ce round, validee par le schema que
    # declare le registre (design §3, §6). Le TYPE, lui, n'est pas ici : il vit
    # dans `deck_snapshot`, qui porte `voteType` et `resolutionStrategy`. Une
    # clef etrangere le dupliquerait, et deux sources de verite finissent par
    # diverger.
    config = models.JSONField(default=dict, blank=True)
```

Migration : `.\.venv\Scripts\python.exe manage.py makemigrations rooms -n round_config`. Schéma seul, aucune donnée.

- [ ] **Step 4: Figer le deck à la préparation**

Dans `prepare_round`, après le `select_deck(room, participant, deck_id)` existant, figer le choix **sur le round** :

```python
    if deck_id is not None:
        select_deck(room, participant, deck_id)
        # Fige le deck SUR LE ROUND des la preparation, et non a l'ouverture :
        # sans cela, preparer un second round avec un autre deck reecrirait
        # celui de la room et changerait le type du premier sous les pieds du
        # facilitateur. C'est ce qui rend un scenario multi-activites possible.
        room.refresh_from_db(fields=["deck_snapshot"])
        rnd.deck_snapshot = room.deck_snapshot
        rnd.save(update_fields=["deck_snapshot"])
```

Dans `open_vote`, ne figer le snapshot que s'il est **absent** (`if rnd.deck_snapshot is None`), au lieu de l'écraser inconditionnellement : un round préparé a déjà le sien.

- [ ] **Step 5: Suite complète**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: **288 passed** (284 + 4). Les tests existants du poker passent sans modification : une room à un seul deck se comporte exactement comme avant.

- [ ] **Step 6: Commit**

```bash
git add rooms/models.py rooms/migrations/0017_round_config.py realtime/services.py realtime/tests/test_round_type.py
git commit -m "Le round fige son deck des la preparation, et porte sa config"
```

---

### Task 2: Le registre valide la configuration

**Files:**
- Modify: `realtime/activities.py` (`config_schema`, `validate_config`)
- Modify: `realtime/services.py` (`configure_round`)
- Test: `realtime/tests/test_round_type.py` (compléter)

**Interfaces:**
- Consumes: `Round.config` (tâche 1), `ActivitySpec` et `validate_payload` (livraison précédente).
- Produces: `activities.validate_config(strategy, config)` ; `services.configure_round(room, participant, round_id, deck_id=None, config=None) -> dict`.

- [ ] **Step 1: Écrire les tests**

Quatre cas : une configuration conforme au schéma est acceptée et persistée ; une clé inconnue est refusée par une `RoomError` de `rejected_type` `"round.configure"` ; un type de valeur erroné est refusé de même ; configurer un round **déjà ouvert** est refusé (la configuration se fige avant l'ouverture, comme le mode de révélation).

- [ ] **Step 2: Lancer, vérifier l'échec** — `services` n'a pas d'attribut `configure_round`.

- [ ] **Step 3: Étendre le registre**

`ActivitySpec` gagne `config_schema: dict[str, type]` (défaut `{}` — le poker n'a aucune option propre aujourd'hui) et la fonction `validate_config(strategy, config)`, bâtie **exactement** sur le modèle de `validate_payload` : mêmes règles, même style de `RoomError`, seul le `rejected_type` diffère. Si les deux fonctions deviennent un copier-coller, extrais leur cœur commun plutôt que de le dupliquer.

- [ ] **Step 4: `configure_round`**

Réservée au facilitateur. Refuse un round inconnu, refuse un round dont l'état n'est pas `idle`, valide la configuration par le registre **avant** d'écrire, applique le deck (en réutilisant le chemin de la tâche 1 plutôt qu'en le réécrivant), et retourne `{"roundId", "deckSnapshot", "config"}`.

- [ ] **Step 5: Suite complète** — Expected: **292 passed**.

- [ ] **Step 6: Commit**

```bash
git commit -m "Le registre valide la configuration d'un round"
```

---

### Task 3: `round.configure` dans le contrat

**Files:**
- Modify: `realtime/consumers.py`
- Modify: `docs/superpowers/specs/delegation-poker-realtime-contract.md`
- Test: `realtime/tests/test_round_type_ws.py`

**Interfaces:**
- Produces (entrant, facilitateur) : `round.configure {roundId, deckId?, config?}`.
- Produces (sortant) : `round.configured {roundId, deckSnapshot, config}`, plus le `deck.changed` existant quand le deck a changé, pour que les clients actuels suivent sans nouveau gestionnaire.

- [ ] **Step 1: Écrire les tests** — trois cas : un facilitateur configure un round préparé et tout le monde reçoit le fait ; un votant se voit refuser l'intention (`rejectedType == "round.configure"`) ; configurer un round ouvert est refusé. Barrière `ping`/`pong`, jamais d'attente temporisée.
- [ ] **Step 2: Lancer, vérifier l'échec** — `Unknown type round.configure`.
- [ ] **Step 3: Brancher** l'intention dans `_dispatch` et étendre le contrat d'une section **sans renuméroter** les existantes.
- [ ] **Step 4: Suite complète** — Expected: **295 passed**.
- [ ] **Step 5: Commit**

```bash
git commit -m "Contrat : round.configure"
```

---

### Task 4: Le front choisit l'activité du round préparé

**Files:** dépôt `Facilitation_frontend` — `src/app/core/realtime/protocol.ts`, `room-socket.service.ts`, `src/app/features/room/activities/delegation-poker/facilitator-panel.component.*`.

**Le panneau de préparation porte déjà un sélecteur de deck** : aujourd'hui il agit sur la room (via `round.prepare {deckId}`), donc sur tous les rounds à venir. Il doit agir sur le **round préparé**.

- [ ] **Step 1** — `protocol.ts` : l'intention `round.configure` et le fait `round.configured`.
- [ ] **Step 2** — Le service émet `round.configure` quand le facilitateur change le deck d'un round déjà préparé, et applique `round.configured`.
- [ ] **Step 3** — Le panneau reflète le deck **du round courant** plutôt que celui de la room. Vérifie ce que `state.sync` fournit : si le deck du round n'y figure pas, dis-le plutôt que de deviner — le serveur devra l'ajouter.
- [ ] **Step 4** — `npm test` et `npx playwright test` verts, aucune spec e2e modifiée.
- [ ] **Step 5** — Commit, sans push ni PR.

---

### Task 5: Documentation

- [ ] `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` : corriger le §3, qui annonce `Round.vote_type` — expliquer que le type vit dans le snapshot du round et pourquoi une clé étrangère le dupliquerait. Marquer 5c faite au §8.
- [ ] `CLAUDE.md` : 5c faite, référence de la suite mise à jour, et la ligne des écarts connus qui cite `Round.vote_type` corrigée.
- [ ] Commit.

---

## Vérification finale

- [ ] `pytest` vert en local et en CI (PostgreSQL 16).
- [ ] **Deux rounds de types différents dans une même room**, joués l'un après l'autre : chacun garde son deck, son dépouillement et son résultat. C'est le critère de la livraison.
- [ ] Une partie de Delegation Poker ordinaire, identique à avant, depuis le front de production.
- [ ] `git grep -n "vote_type" -- "*.py" | grep -v migrations` ne renvoie que `Room.vote_type` (la valeur par défaut à la création) et les tables `decks`.
