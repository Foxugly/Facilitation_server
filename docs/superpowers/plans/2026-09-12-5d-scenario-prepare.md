# Livraison 5d — le scénario se prépare en amont

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qu'un facilitateur compose sa séance **avant** qu'elle commence — une file de rounds ordonnée, qu'il réordonne et élague librement — puis la déroule. C'est le modèle Wooclap, et c'est la demande produit d'origine : « préparer le scénario en amont et le jouer ».

**Architecture:** La file existe déjà : la salle porte N rounds, chacun avec ses items, son type et sa configuration, et l'agenda les diffuse. Ce qui manque est **l'ordre explicite** — aujourd'hui l'agenda suit la date de création, qu'on ne peut donc pas changer — et les deux gestes qui font d'une liste un scénario : **réordonner** et **retirer**. Cette livraison ajoute `Round.sequence` et ces deux intentions. Aucun nouvel état de cycle : un round préparé reste un round `idle` qui n'est pas le round courant.

**Tech Stack:** Django 5 + DRF + Channels (daphne), PostgreSQL en CI/prod, sqlite en dev, pytest. Front : Angular 21 (`Facilitation_frontend`), Playwright.

**Spec:** `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` (§1 le produit, §5 contrat, §8 découpage)

## Deux décisions qui s'écartent du design

**1. Le message `agenda` n'est pas renommé `scenario`.** Le design prévoit ce renommage en 5d. Je n'y procède pas : « agenda » décrit exactement ce que la chose est — l'ordre du jour de la salle — ne porte aucun vocabulaire banni, et le renommer imposerait un déploiement en trois temps (back tolérant les deux noms, front bascule, back nettoie) pour **aucun** gain fonctionnel. C'est l'arbitrage déjà rendu pour `session.join`, conservé parce que le renommer aurait cassé le SPA sans rien apporter. Le vocabulaire de domaine — `Round`, `Item`, `Response` — a été renommé parce qu'il portait une ambiguïté réelle ; ce n'est pas le cas ici.

**2. Pas d'états `DRAFT` / `READY`.** Le design les annonce ; le cycle actuel les rend inutiles. Un round préparé **est** un round `idle` qui n'est pas `Room.current_round` — la distinction que `DRAFT`/`READY` apporterait n'a aucun consommateur tant qu'aucune activité ne s'arrête avant la révélation. Introduire deux états sans consommateur, c'est ajouter des transitions à tester et à maintenir pour rien. À rouvrir quand la première activité sans révélation arrivera, pas avant.

## Global Constraints

- **`pytest` vert à chaque commit.** Référence d'entrée : **307 passed**.
- Commandes : `.\.venv\Scripts\python.exe -m pytest -q`, `.\.venv\Scripts\python.exe manage.py <cmd>` (Windows, venv Python 3.14).
- **Le poker doit rester jouable après chaque tâche**, y compris depuis le front déployé en production — qui ne connaîtra les nouvelles intentions qu'à la tâche 4.
- **Aucun message de commit accentué** ; commentaires de code en français sans accents.
- **Une migration ne mélange jamais écriture de données et `ALTER TABLE`** : PostgreSQL refuse l'`ALTER TABLE` tant que des événements de trigger restent en attente. Deux migrations séparées — ce dépôt a déjà payé cette erreur en CI.
- **Tests async du consumer : jamais d'`asyncio.sleep()`** pour attendre un fait ; la barrière est un aller-retour `ping`/`pong`. `_drain_until` abandonne au bout de 8 messages.
- Le serveur fait autorité : intention de contrôle réservée au facilitateur, `RoomError(code, message, rejected_type)` sur un coup illégal.
- Le mot « Session » reste banni du domaine, sauf le message `session.join`.

---

### Task 1: `Round.sequence`, l'ordre explicite

**Files:**
- Modify: `rooms/models.py`
- Create: `rooms/migrations/0018_round_sequence.py` (schéma), `rooms/migrations/0019_backfill_round_sequence.py` (données)
- Modify: `rooms/migration_ops.py`, `realtime/services.py` (`build_agenda`, `_new_round`)
- Test: `rooms/tests/test_round_sequence.py`

**Interfaces:**
- Produces: `Round.sequence` (PositiveSmallInteger, défaut 1) ; l'agenda ordonné par `(sequence, id)` ; tout round neuf prend la séquence suivante de sa salle.

- [ ] **Step 1: Écrire les tests**

Quatre cas, chacun avec une valeur concrète : trois rounds créés prennent les séquences 1, 2, 3 ; l'agenda les rend dans l'ordre des séquences et non de la création (fabrique des séquences qui contredisent l'ordre de création, sinon le test ne prouve rien) ; la migration de données attribue les séquences dans l'ordre de création existant ; un round retiré ne laisse pas de trou qui casse l'ordre.

- [ ] **Step 2: Lancer, vérifier l'échec.**

- [ ] **Step 3: Le champ et l'ordre.** `Round.sequence`, l'ordre de `build_agenda` passé à `("sequence", "id")`, et `_new_round` qui attribue `room.rounds.count() + 1`. Attention : `_replay_round` crée aussi un round — il doit prendre la séquence suivante, pas celle de sa source.

- [ ] **Step 4: Les deux migrations.** `0018` ajoute le champ (schéma seul). `0019` remplit les séquences dans l'ordre `(created_at, id)` par salle, via une fonction de `rooms/migration_ops.py` qui ne lit aucun modèle concret. **Deux fichiers, pas un.**

- [ ] **Step 5: Suite complète** — Expected: **311 passed**.

- [ ] **Step 6: Commit**

```bash
git commit -m "Round.sequence : le scenario a un ordre explicite"
```

---

### Task 2: réordonner et retirer

**Files:** `realtime/services.py`, test `realtime/tests/test_scenario.py`

**Interfaces:**
- Produces: `services.reorder_rounds(room, participant, round_ids) -> list[dict]` et `services.remove_round(room, participant, round_id) -> int`.

- [ ] **Step 1: Écrire les tests.** Ce qu'ils doivent démontrer :
  1. Réordonner renumérote les séquences de façon contiguë, et l'agenda suit.
  2. Une liste d'identifiants qui ne correspond pas exactement au jeu des rounds de la salle est refusée — **y compris une liste comportant un doublon**, piège déjà rencontré dans ce dépôt sur le réordonnancement des items : comparer des ensembles ne suffit pas, il faut aussi comparer les longueurs.
  3. Retirer un round préparé le fait disparaître de l'agenda.
  4. **Retirer un round qui porte un résultat est refusé** : l'historique ne se réécrit pas. Même raison que pour un item déjà acté.
  5. Retirer le round **courant** : décide du comportement et écris-le. Deux lectures défendables — le refuser, ou l'accepter en désignant un autre round comme courant. Choisis, justifie dans le code, et teste ce que tu as choisi.
  6. Un votant se voit refuser les deux intentions.

- [ ] **Step 2: Lancer, vérifier l'échec.**
- [ ] **Step 3: Implémenter**, en réutilisant les gardes existantes plutôt qu'en les réécrivant. Regarde comment `reorder_items` valide son jeu d'identifiants : la même règle s'applique, au niveau des rounds.
- [ ] **Step 4: Suite complète** — Expected: **~317 passed**.
- [ ] **Step 5: Commit**

```bash
git commit -m "Le scenario se reordonne et s'elague"
```

---

### Task 3: les intentions au contrat

**Files:** `realtime/consumers.py`, `docs/superpowers/specs/delegation-poker-realtime-contract.md`, test `realtime/tests/test_scenario_ws.py`

**Interfaces:**
- Produces (entrants, facilitateur) : `round.reorder {roundIds: []}`, `round.remove {roundId}`.
- Produces (sortant) : l'agenda rediffusé — c'est déjà le message que le front applique, **aucun fait nouveau n'est nécessaire**. Vérifie-le plutôt que de l'inventer.

- [ ] **Step 1: Écrire les tests** — réordonnancement diffusé à tous, retrait diffusé à tous, refus à un votant, refus de retirer un round acté. Barrière `ping`/`pong`.
- [ ] **Step 2: Lancer, vérifier l'échec.**
- [ ] **Step 3: Brancher**, étendre le contrat **sans renuméroter** aucune section.
- [ ] **Step 4: Suite complète** — Expected: **~321 passed**.
- [ ] **Step 5: Commit**

---

### Task 4: le front compose le scénario

**Files:** dépôt `Facilitation_frontend` — protocole, service de socket, panneau du facilitateur.

La liste d'agenda existe déjà à l'écran. Il lui manque deux gestes : **monter / descendre** une entrée, et **la retirer**.

- [ ] **Step 1** — Protocole : les deux intentions.
- [ ] **Step 2** — Service : émission, et application de l'agenda rediffusé (probablement déjà en place — vérifie avant d'ajouter).
- [ ] **Step 3** — Panneau : deux boutons de déplacement et un de retrait par entrée. **Pas de glisser-déposer** : la conception l'a écarté pour le tactile (le glisser-déposer de la bibliothèque repose sur l'API HTML5, dont le support tactile est faible), et des boutons haut/bas sont le repli explicitement accepté. Ne l'introduis pas « parce que c'est plus élégant ».
- [ ] **Step 4** — Le retrait d'un round acté ne doit pas être proposé : l'agenda dit quel round est `done`. Mieux vaut ne pas offrir le geste que de le faire refuser par le serveur.
- [ ] **Step 5** — Tests unitaires des deux gestes, **avec preuve par mutation**. Les specs e2e ne changent pas.
- [ ] **Step 6** — Commit, sans push ni PR.

---

### Task 5: Documentation

- [ ] `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` : marquer 5d faite ; consigner les deux décisions d'écart (pas de renommage `agenda` → `scenario`, pas d'états `DRAFT`/`READY`) **avec leur motif**, pour qu'on ne les prenne pas pour des oublis.
- [ ] `CLAUDE.md` : 5d faite, référence de la suite mise à jour.
- [ ] Commit.

---

## Vérification finale

- [ ] `pytest` vert en local et en CI (PostgreSQL 16).
- [ ] **Un scénario de trois rounds composé avant l'ouverture de la salle, réordonné, élagué, puis joué dans l'ordre voulu.** C'est le critère de la livraison.
- [ ] Une partie de Delegation Poker ordinaire, inchangée, depuis le front de production.
- [ ] Un round acté ne peut être retiré ni par le front ni par le serveur.
