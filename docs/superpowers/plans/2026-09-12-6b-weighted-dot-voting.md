# Livraison 6b — Weighted Dot Voting

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer la seconde activité de vote par jetons — n jetons de poids 1, 3, 5, 7… au plus un par item — et, ce faisant, **mesurer** la promesse du registre là où 6a l'avait laissée ouverte.

**Architecture:** 6a a livré toute la plomberie : activité sans cartes, validation à l'échelle du round, résultat en payload, totaux en direct, reste à placer, classement figé, chaînage « top N ». 6b ne devrait donc ajouter qu'**une entrée de registre et un libellé**. Là où elle débordera, l'abstraction fuit — et on saura précisément où.

**Tech Stack:** Django 5 + DRF + Channels (daphne), PostgreSQL en CI/prod, sqlite en dev, pytest. Front : Angular 21 (`Facilitation_frontend`), Playwright.

**Spec:** `docs/superpowers/specs/2026-09-12-dot-voting-design.md` — §1 tranche les règles des deux activités, §9 consigne le bilan de 6a et l'avertissement de mesure qui gouverne cette livraison.

## La mesure, et pourquoi ce plan la met en premier

Le bilan de 6a (§9 du design) prévient que **6b sera un faux positif** si on la mesure en fichiers touchés : jumelle de 6a par construction, elle réutilise les mêmes champs. La mesure honnête a été fixée d'avance, avant d'écrire une ligne :

- **Serveur :** nombre de lignes ajoutées à `realtime/services.py`. Attendu : **zéro**.
- **Client :** nombre de lignes ajoutées à `src/app/core/realtime/protocol.ts`. 6a en a ajouté **87** (le fichier est passé à 429 lignes). C'est le chiffre à battre.

**Un point est déjà connu pour déborder, et ce n'est pas une surprise :** `remaining_budget` rend un **entier** pour `dot_voting_v1` (« il te reste 4 gommettes ») alors que pour la variante pondérée le reste à placer est **l'ensemble des poids non encore utilisés** (« il te reste 3, 7 et 9 »). Le champ du registre est déclaré JSON-sérialisable, donc le serveur l'encaisse sans rien ouvrir ; c'est le **client** qui devra élargir son type. C'est exactement la fuite que §9 annonçait, et la livraison doit la mesurer, pas la contourner.

## Ce que la conception a déjà tranché

À ne pas re-décider :

- **n jetons**, de poids 1, 3, 5, 7, 9… — le k-ième poids vaut `2k - 1`, pour `n` = nombre d'items du round.
- **Au plus un jeton par item.**
- **Chaque poids au plus une fois** sur l'ensemble du round.
- Les jetons **ne sont pas obligatoires** (règle héritée de 6a, §4).
- **Dépouillement identique** à 6a : somme des points par item, classement figé à la révélation.
- **Le facilitateur décide de la visibilité** des totaux ; le défaut est le secret.
- Code du type : **`weighted_dot_voting`**, stratégie **`weighted_dot_voting_v1`** (§1).
- **Pas de glisser-déposer.**

## Global Constraints

- **`pytest` vert à chaque commit.** Référence d'entrée : **475 passed, 1 skipped**.
- Commandes : `.\.venv\Scripts\python.exe -m pytest -q`, `.\.venv\Scripts\python.exe manage.py <cmd>`.
- **Le poker et le Dot Voting simple doivent rester identiques.** Une régression sur l'un des deux est un échec de la livraison.
- **Aucun message de commit accentué** ; commentaires de code en français sans accents ; le caractère `§` s'écrit **littéralement** et se vérifie par `grep` après écriture (transcrit par erreur quatre fois dans ce projet).
- Une migration ne mélange jamais schéma et données ; une migration de données n'utilise que `apps.get_model`.
- **Tests async du consumer : jamais d'`asyncio.sleep()` ni de `_settle()` pour attendre un fait.** La barrière est un aller-retour `ping`/`pong` (`_ping_pong_types(comm, after=[...])`), qui **n'ordonne que la connexion qui l'émet** ; sur la connexion qui a déclenché l'action, il en faut **deux**. Pour attendre une tâche de timer, sonder la condition (`_wait_for_timer_task`), jamais compter des ticks.
- **Un client ne doit jamais se voir proposer un geste que le serveur refusera.**
- **Chaque fois qu'une règle devrait atterrir dans `services.py`, arrête-toi : c'est la mesure même de cette livraison qui est en jeu.** Si tu dois vraiment y toucher, dis-le en gros dans ton rapport avec le nombre de lignes.

---

### Task 1 : l'entrée de registre, et ce qu'elle partage

**Files:** `realtime/activities.py`, `realtime/tests/test_weighted_dot_voting.py`

Trois fonctions sont propres à la variante pondérée ; **tout le reste se réutilise tel quel** — `aggregate`, `response_view`, `freeze_results`, `validate_chosen_value`, `history_entry`, `rank_value` et le `config_schema`. **Réutilise les callables existantes, ne les recopie pas** : deux copies d'une même règle finiraient par diverger, et c'est précisément ce que le registre existe pour empêcher. Si une réutilisation te semble abusive, dis pourquoi plutôt que de dupliquer.

Ce qui change :

1. `validate_value` — `points` vaut 0, ou l'un des poids `1, 3, 5, … 2n-1`. Un poids pair, négatif, ou au-delà de `2n-1` est refusé. Attention au piège déjà payé en 6a : `isinstance(True, int)` est vrai en Python, un booléen doit être refusé.
2. `validate_responses` — sur l'ensemble du round, **aucun poids non nul n'est utilisé deux fois**. Même piège qu'en 6a, et il est ici encore plus mordant : `existing` porte l'entrée de l'item en cours si une réponse y existe déjà, et `cast_response` **remplace** — corriger son propre jeton sur le même item ne doit donc pas se voir refuser pour « poids déjà utilisé » par sa propre valeur précédente. C'est le test central de la tâche, à **prouver par mutation**.
3. `remaining_budget` — la **liste des poids encore disponibles**, ordonnée, et non un entier. Lis la mise en garde de la section « mesure » ci-dessus : c'est le point de fuite connu.

- [ ] **Step 1** — Tests : les bornes de `validate_value` (0 accepté, poids valides acceptés, poids pair refusé, `2n+1` refusé, booléen refusé) ; l'unicité des poids sur le round ; **la correction d'un jeton sur le même item reste acceptée** (mutation) ; le reste à placer est la liste des poids libres ; l'agrégation et le classement se comportent comme en 6a (mêmes fonctions — un test qui le constate suffit, ne réécris pas la batterie de 6a).
- [ ] **Step 2** — Lancer, vérifier l'échec.
- [ ] **Step 3** — Implémenter. **Aucune ligne dans `services.py`** : si tu crois en avoir besoin, arrête-toi et dis-le.
- [ ] **Step 4** — Suite complète, puis commit.

---

### Task 2 : le deck, et sa mise en catalogue

**Files:** `decks/seed.py`, `decks/management/commands/seed_weighted_dot_voting_deck.py`, migration de données, `deploy/deploy.sh`, tests

Un deck sans carte portant `voteType: "weighted_dot_voting"` et `resolutionStrategy: "weighted_dot_voting_v1"`. **Suis exactement le motif du deck `dot_voting`**, tâche 1 de 6a puis tâche 7 : commande idempotente, `free_tier=False`, et cette fois `is_active=True` **dès le seed** — l'entrée de registre existe avant le deck, l'ordre des deux tâches de ce plan est fait pour ça.

Les noms traduits dans les cinq langues (en/fr/es/it/nl), sur le modèle de `DOT_VOTING_NAMES`.

- [ ] **Step 1** — Tests : le snapshot porte le bon type et `cards: []` ; la commande est idempotente ; le deck apparaît au catalogue d'une équipe et **pas** au catalogue gratuit ; un rejeu ne revient jamais sur une désactivation délibérée.
- [ ] **Step 2** — Lancer, vérifier l'échec.
- [ ] **Step 3** — Implémenter, et **ajouter la commande à `deploy/deploy.sh`** — sans quoi le deck n'existe pas en production et la livraison n'est pas livrée.
- [ ] **Step 4** — Suite complète, puis commit.

---

### Task 3 : jouer la partie de bout en bout, côté serveur

**Files:** `realtime/tests/test_weighted_dot_voting_ws.py`

Aucun code de production attendu ici. C'est le test qui **prouve la promesse** : une partie complète de Weighted Dot Voting doit se jouer à travers le consumer **sans qu'une seule ligne ait été ajoutée à `realtime/consumers.py` ni à `realtime/services.py`**.

- [ ] **Step 1** — Un round de trois items, deux participants, des poids répartis, la visibilité des totaux réglée par le facilitateur, une révélation, un classement figé, et le classement repris par un chaînage « top 2 ». Barrière `ping`/`pong` avec `after=[…]` explicite ; jamais de `_settle()`.
- [ ] **Step 2** — Lancer. **Si ce test passe du premier coup**, c'est le résultat de la livraison : dis-le tel quel dans ton rapport, avec le `git diff --numstat` de `services.py` et `consumers.py`.
- [ ] **Step 3** — Suite complète, puis commit.

---

### Task 4 : le front

**Files:** dépôt `Facilitation_frontend`.

- [ ] Le registre front déclare `weighted_dot_voting` et **réutilise les composants de Dot Voting** — c'est le même plateau, le même panneau. Si tu dois les dupliquer, c'est que le composant était trop spécifique : dis-le.
- [ ] La main du participant montre **les poids qu'il lui reste**, pas un compte de gommettes. C'est le point où le type du protocole s'élargit.
- [ ] **Mesure obligatoire dans le rapport** : `git diff --numstat` sur `src/app/core/realtime/protocol.ts`, à comparer aux **87 lignes** de 6a.
- [ ] **Pas de glisser-déposer.**
- [ ] Tests unitaires **avec preuve par mutation**, totaux avant/après. Specs e2e inchangées.
- [ ] Commit, sans push ni PR.

---

### Task 5 : Documentation

- [ ] `docs/superpowers/specs/2026-09-12-dot-voting-design.md` : marquer 6b faite, et **compléter §9 avec la mesure réelle** — lignes ajoutées à `services.py`, à `consumers.py`, à `protocol.ts`. C'est la réponse à la question que 6a avait posée.
- [ ] `CLAUDE.md` : 6b faite, référence de la suite, étape suivante.
- [ ] Commit.

---

## Vérification finale

- [ ] `pytest` vert en local et en CI (PostgreSQL 16).
- [ ] **Une partie de Weighted Dot Voting jouée de bout en bout** : trois items, deux participants, des poids répartis, une révélation, un classement.
- [ ] **Le chaînage « top N » reprend ce classement.**
- [ ] **Un Dot Voting simple et une partie de Delegation Poker inchangés**, depuis le front de production.
- [ ] **Le chiffre**, écrit noir sur blanc : ce que 6b a coûté en lignes hors registre.
