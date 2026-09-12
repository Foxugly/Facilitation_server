# Livraison 6a — Dot Voting

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer la première activité neuve du produit — un vote par gommettes qui produit un classement — et, ce faisant, éprouver la promesse du registre : **ajouter une activité ne doit toucher que lui**.

**Architecture:** Presque tout existe. Un round porte N items, chaque participant y répond par un payload JSON validé par le registre, l'agrégation et le classement y sont déclarés, le chaînage sait reprendre un classement. Cette livraison ajoute une activité **sans cartes**, une **validation à l'échelle du round** (la seule ouverture réelle demandée au domaine), un **résultat en payload**, des **totaux visibles en direct** au choix du facilitateur, et une **vue du facilitateur** sur ce qu'il reste à placer.

**Tech Stack:** Django 5 + DRF + Channels (daphne), PostgreSQL en CI/prod, sqlite en dev, pytest. Front : Angular 21 (`Facilitation_frontend`), Playwright.

**Spec:** `docs/superpowers/specs/2026-09-12-dot-voting-design.md` — écrite avec l'utilisateur, elle tranche les règles du produit et ne se re-décide pas.

## Global Constraints

- **`pytest` vert à chaque commit.** Référence d'entrée : **381 passed**.
- Commandes : `.\.venv\Scripts\python.exe -m pytest -q`, `.\.venv\Scripts\python.exe manage.py <cmd>`.
- **Le poker doit rester identique** — dépouillement, résultat, contrat. Une régression sur lui est un échec de la livraison, pas un dommage collatéral acceptable.
- **Aucun message de commit accentué** ; commentaires de code en français sans accents ; le caractère `§` s'écrit **littéralement** et se vérifie par `grep` après écriture (il a été transcrit par erreur trois fois dans ce projet).
- **Une migration ne mélange jamais schéma et données** — piège déjà payé en CI.
- **Tests async du consumer : jamais d'`asyncio.sleep()`** ; la barrière est un aller-retour `ping`/`pong`, **et elle n'ordonne que la connexion qui l'émet**.
- **Ce qui est réservé au facilitateur est filtré à l'émission**, jamais diffusé au groupe en comptant sur le client.
- **Un client ne doit jamais se voir proposer un geste que le serveur refusera.**
- **Chaque fois qu'une règle d'activité devrait atterrir dans `services.py`, arrête-toi et demande-toi si le registre ne devrait pas la porter.** C'est l'objet même de cette livraison.

---

### Task 1: une activité sans cartes

**Files:** `decks/seed.py`, une commande de seed, `rooms/tests/test_dot_voting_deck.py`

Le type `dot_voting` et son « deck » sans carte active. Le snapshot doit porter `voteType: "dot_voting"`, `resolutionStrategy: "dot_voting_v1"` et `cards: []`.

- [ ] **Step 1** — Tests : le snapshot d'un tel deck porte le bon type et aucune carte ; la commande de seed est **idempotente** (jouée deux fois, elle ne duplique rien) — les commandes existantes le sont, suis leur motif.
- [ ] **Step 2** — Lancer, vérifier l'échec.
- [ ] **Step 3** — Implémenter en suivant `decks/seed.py`, qui crée déjà des types et des decks. Ne réinvente pas la structure.
- [ ] **Step 4** — Suite complète, puis commit.

---

### Task 2: le registre porte l'activité

**Files:** `realtime/activities.py`, `realtime/tests/test_dot_voting.py`

L'entrée `dot_voting_v1` déclare : le schéma de payload (`points`, entier), la validation de valeur (0 ≤ points ≤ n, **en remplacement** de l'appartenance au deck, inapplicable), la configuration (visibilité), l'agrégation (somme des points par item), le classement, et ce que l'activité consomme et produit.

**Le point délicat : `n` est le nombre d'items du round**, que le registre ne connaît pas. Regarde les signatures existantes avant d'inventer : soit la validation reçoit ce dont elle a besoin, soit le registre expose une fonction que le domaine appelle avec le contexte. **Choisis, et écris pourquoi** — c'est la forme que toutes les activités suivantes reprendront.

- [ ] **Step 1** — Tests : une valeur dans les bornes est acceptée, au-delà refusée ; l'agrégation somme par item ; le classement ordonne du plus haut au plus bas et **départage les ex æquo de façon déterministe** (dis comment, et teste-le : un classement instable rendrait l'historique incohérent) ; le poker n'est pas affecté.
- [ ] **Step 2** — Lancer, vérifier l'échec.
- [ ] **Step 3** — Implémenter.
- [ ] **Step 4** — Suite complète, puis commit.

---

### Task 3: la validation à l'échelle du round

**Files:** `realtime/activities.py`, `realtime/services.py`, `realtime/tests/test_dot_voting.py`

C'est **la seule ouverture réelle** que cette livraison demande au domaine : aujourd'hui `cast_response` valide une réponse à la fois, alors que la somme des jetons d'un participant se juge sur l'ensemble du round.

Le registre gagne un point d'accroche à cette échelle, dont le défaut ne contraint rien (le poker n'a aucune règle de ce genre). `cast_response` l'appelle **avant d'écrire**, en lui donnant les réponses déjà posées par ce participant sur ce round.

- [ ] **Step 1** — Tests : un participant qui dépasse son budget est refusé et **rien n'est écrit** ; il peut répartir librement en deçà ; **modifier** une réponse existante ne doit pas compter deux fois l'ancienne valeur (c'est le piège de cette tâche — le budget se calcule sur l'état **après** remplacement). Vérifie ce dernier par mutation.
- [ ] **Step 2** — Lancer, vérifier l'échec.
- [ ] **Step 3** — Implémenter. Un refus lève `RoomError` avec le type d'intention émis.
- [ ] **Step 4** — Suite complète, puis commit.

---

### Task 4: le résultat devient un payload

**Files:** `rooms/models.py`, migration, `realtime/services.py`, tests

`Result` ne porte qu'une valeur de carte. Il lui faut un **payload**, additif : le poker garde son champ, Dot Voting y écrit son classement, figé à la révélation.

- [ ] **Step 1** — Tests : le classement d'un round révélé est figé et **ne bouge plus** si une réponse change ensuite (vérifie par mutation — c'est l'invariant du figement) ; le poker écrit toujours son résultat comme avant ; l'historique continue de lire le poker sans changement.
- [ ] **Step 2** — Lancer, vérifier l'échec.
- [ ] **Step 3** — Implémenter. Migration **schéma seul**.
- [ ] **Step 4** — Suite complète, puis commit.

---

### Task 5: les totaux en direct, et la vue du facilitateur

**Files:** `realtime/services.py`, `realtime/consumers.py`, contrat, tests

Deux diffusions nouvelles, de portées différentes :

- **les totaux par item**, à tous, **uniquement si** la configuration du round l'autorise. Jamais le lien participant → jetons : l'invariant du secret tient parce qu'on n'émet qu'un **agrégat**. Le défaut est le secret.
- **ce qu'il reste à placer par participant**, au **facilitateur seul**, filtré à l'émission.

- [ ] **Step 1** — Tests : en mode secret, aucun total ne part avant la révélation — **prouve l'absence par une barrière en aller-retour sur la connexion du votant**, pas par une inspection précoce ; en mode visible, les totaux partent et ne contiennent **aucune** identité ; le reste à placer n'atteint jamais un votant. Vérifie les trois par mutation.
- [ ] **Step 2** — Lancer, vérifier l'échec.
- [ ] **Step 3** — Implémenter, étendre le contrat **sans renuméroter**.
- [ ] **Step 4** — Suite complète, puis commit.

---

### Task 6: le front

**Files:** dépôt `Facilitation_frontend`.

- [ ] Le registre front déclare l'activité et résout ses composants — **c'est là que se mesure la promesse côté client aussi**.
- [ ] Le plateau : les items, les gommettes posées, les totaux quand ils sont visibles, le classement après révélation.
- [ ] La main du participant : poser et retirer ses gommettes, voir ce qui lui reste.
- [ ] Le panneau du facilitateur : le réglage de visibilité, le reste à placer par participant, la révélation.
- [ ] **Pas de glisser-déposer.** Décision de conception, prise pour le tactile.
- [ ] Tests unitaires **avec preuve par mutation**, totaux avant/après. Specs e2e inchangées.
- [ ] Commit, sans push ni PR.

---

### Task 7: Documentation

- [ ] Marquer l'étape faite, et **répondre par écrit à la question que cette livraison posait** : qu'a-t-il fallu ouvrir hors du registre, et pourquoi ? C'est le résultat le plus utile de l'étape.
- [ ] `CLAUDE.md` : la référence de la suite, et l'étape suivante.
- [ ] Commit.

---

## Vérification finale

- [ ] `pytest` vert en local et en CI (PostgreSQL 16).
- [ ] **Un Dot Voting joué de bout en bout** : trois items, deux participants, des gommettes réparties, une révélation, un classement.
- [ ] **Le classement alimente un chaînage** : le « top N », inutilisable jusqu'ici, fonctionne enfin.
- [ ] **Une partie de Delegation Poker inchangée**, depuis le front de production.
- [ ] Le bilan écrit de ce qui a dû sortir du registre.
