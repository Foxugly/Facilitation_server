# Révélation anonyme — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** à la révélation, ne plus montrer qui a voté quoi — afficher le nombre de voix par valeur, uniquement pour les valeurs ayant au moins une voix.

**Architecture :** l'anonymat est obtenu **en cessant d'émettre la donnée**, pas en la masquant côté client. `revealed_payload` renvoie un décompte agrégé ; la clé `votes`, qui portait les couples participant/carte, disparaît du protocole.

**Tech Stack :** Django 6 + Channels + pytest ; Angular 21 standalone + signals + Vitest.

> ### ⚠️ Amendé le 2026-07-19 — l'anonymat est devenu une **option de round**
>
> Décision produit postérieure à ce plan : la révélation est **nominative par défaut**, et
> l'anonymat est une option que le facilitateur pose avant d'ouvrir le round, réservée aux
> **équipes payantes**. Une salle gratuite (sans compte) reste donc nominative.
>
> Ce qui change : `revealed_payload` réémet la clé `votes` **quand le round est nominatif**.
> Ce qui ne change pas : le principe ci-dessous vaut toujours **dans le mode anonyme** — on
> n'y masque rien côté client, on cesse d'émettre. Voir `VoteSession.is_anonymous` et
> `realtime/tests/test_reveal_mode.py`.
>
> Garde-fou ajouté : le mode est **figé à l'ouverture** et **annoncé à tous** dans `state.sync`
> (`reveal.anonymous`), pas seulement au facilitateur — un votant doit savoir si sa carte
> sortira avec son nom *avant* de la jouer.

## Le principe, à ne jamais contourner

Masquer le tableau côté client serait de la façade : une trame WebSocket est lisible dans les outils de
développement de n'importe quel navigateur. Tant que le serveur envoie « participant X a voté 4 »,
les votes ne sont pas anonymes, quelle que soit l'interface.

Dans un round **anonyme**, le serveur ne doit donc jamais émettre de lien participant → carte.

## Global Constraints

- `revealed_payload(room)` renvoie `{"tally": [{"cardValue": str, "count": int}, ...], "spread": {...}}`.
  **La clé `votes` disparaît.**
- Le décompte ne contient **que les valeurs ayant au moins une voix**. Une valeur à zéro n'apparaît pas.
- Ordre du décompte : celui du deck, pour un affichage stable d'une révélation à l'autre.
- `spread` (min/max) est agrégé : il reste.
- `participation.update` continue d'indiquer **qui** a voté — c'est différent de **quoi**, et le
  facilitateur en a besoin pour relancer les retardataires. Ne pas y toucher.
- **`PROTOCOL_VERSION` reste à `1`.** Le passer à `2` ferait rejeter tous les clients existants par le
  consumer, ce qui serait pire que la dégradation temporaire décrite ci-dessous.
- **Fenêtre de déploiement assumée :** entre la mise en production du backend et celle du frontend,
  l'ancien SPA lira une clé `votes` absente et affichera une révélation vide. Pas de plantage. Déployer
  le frontend juste après le backend.
- Tests backend : pytest, `@pytest.mark.django_db`, fixtures locales. Aucun linter dans ce repo.
  Commande : `.venv/Scripts/python -m pytest`
- Tests frontend : Vitest, **aucun `TestBed` dans ce repo**. `npm test` **et** `npm run build`.
- i18n : cinq catalogues `public/i18n/{fr,nl,en,it,es}.json` ; `src/app/i18n-parity.spec.ts` casse le
  build en cas d'oubli.
- **Fichier hors périmètre**, modifié-non-commité et appartenant au propriétaire :
  `Poker_frontend/src/app/features/about/contact.ts`. Ne pas le committer.
- Branches : `feat/bypass-and-tables` (`Poker_server`), `feat/subscription-bypass` (`Poker_frontend`).
  Ne rien pousser.

---

### Task 1 : décompte anonyme côté serveur

**Repo :** `D:\Projects\PycharmProjects\Poker_server`

**Files:**
- Modify: `realtime/services.py` (`revealed_payload`)
- Test: `realtime/tests/test_timer.py` (ajouts — le fichier possède déjà une fixture `room_with_facilitator`)

**Interfaces:**
- Produit : `revealed_payload(room) -> {"tally": [{"cardValue", "count"}], "spread": {"min", "max"}}`.

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter `import json` en tête de `realtime/tests/test_timer.py`, puis :

```python
@pytest.mark.django_db
def test_revealed_payload_is_anonymous(room_with_facilitator):
    """La charge utile ne doit contenir aucun lien participant -> carte."""
    room, facilitator, voter = room_with_facilitator
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    services.cast_vote(room, facilitator, "4")
    services.cast_vote(room, voter, "4")
    services.reveal(room, facilitator)

    payload = services.revealed_payload(room)
    assert "votes" not in payload
    serialized = json.dumps(payload)
    assert str(voter.public_id) not in serialized
    assert str(facilitator.public_id) not in serialized


@pytest.mark.django_db
def test_revealed_payload_counts_votes_per_value(room_with_facilitator):
    room, facilitator, voter = room_with_facilitator
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    services.cast_vote(room, facilitator, "4")
    services.cast_vote(room, voter, "4")
    services.reveal(room, facilitator)

    assert services.revealed_payload(room)["tally"] == [{"cardValue": "4", "count": 2}]


@pytest.mark.django_db
def test_revealed_payload_omits_values_without_votes(room_with_facilitator):
    """Seules les valeurs ayant au moins une voix apparaissent."""
    room, facilitator, voter = room_with_facilitator
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    services.cast_vote(room, facilitator, "1")
    services.cast_vote(room, voter, "7")
    services.reveal(room, facilitator)

    tally = services.revealed_payload(room)["tally"]
    assert [entry["cardValue"] for entry in tally] == ["1", "7"]
    assert all(entry["count"] >= 1 for entry in tally)


@pytest.mark.django_db
def test_revealed_payload_empty_when_no_votes(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    services.set_timer(room, facilitator, True, 10)
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    session = services._current_session(room)
    session.vote_deadline = timezone.now() - timezone.timedelta(seconds=1)
    session.save(update_fields=["vote_deadline"])
    services.reveal_on_timeout(room)

    payload = services.revealed_payload(room)
    assert payload["tally"] == [] and payload["spread"]["min"] is None
```

Les valeurs de carte `"1"`, `"4"` et `"7"` supposent le deck standard : **vérifie les valeurs réelles**
renvoyées par `_card_values(room)` et adapte si nécessaire, plutôt que de faire échouer un test sur une
carte inexistante.

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/python -m pytest realtime/tests/test_timer.py -q -k "anonymous or tally or payload"`
Expected: FAIL — `KeyError: 'tally'`

- [ ] **Step 3 : réécrire `revealed_payload`**

Remplacer intégralement la fonction dans `realtime/services.py`, et ajouter `from collections import Counter`
en tête du module :

```python
def revealed_payload(room):
    """Decompte anonyme des votes — ONLY ever called in REVEALED state
    (secret-of-votes, contract §6.a).

    Ne renvoie AUCUN lien participant -> carte : l'anonymat s'obtient en n'emettant
    pas la donnee, pas en la masquant cote client (une trame WS est lisible dans les
    outils de developpement du navigateur). Seules les valeurs ayant au moins une
    voix figurent dans le decompte, dans l'ordre du deck.
    """
    session = _current_session(room)
    votes = list(Vote.objects.filter(session=session))
    counts = Counter(v.card_value for v in votes)
    tally = [
        {"cardValue": value, "count": counts[value]}
        for value in _card_values(room)
        if counts.get(value)
    ]
    numeric = [int(v.card_value) for v in votes if v.card_value.isdigit()]
    spread = {"min": min(numeric), "max": max(numeric)} if numeric else {"min": None, "max": None}
    return {"tally": tally, "spread": spread}
```

**Vérifie que `_card_values(room)` renvoie les valeurs dans l'ordre du deck.** Si son ordre n'est pas
garanti, trie explicitement plutôt que de dépendre d'un ordre d'insertion.

- [ ] **Step 4 : vérifier qu'aucun autre code ne dépend de la clé `votes`**

Run: `grep -rn "revealed_payload" --include=*.py .`
Run: `grep -rn "\"votes\"" --include=*.py realtime/ rooms/ history/`
Expected: aucun consommateur restant qui lirait `payload["votes"]`. Corriger le cas échéant.

- [ ] **Step 5 : suite complète**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, aucune régression

- [ ] **Step 6 : commit**

```bash
git add realtime/
git commit -m "feat(vote): revelation anonyme, decompte par valeur au lieu des votes nominatifs"
```

---

### Task 2 : affichage anonyme dans la room

**Repo :** `D:\Projects\WebstormProjects\Poker_frontend`

**Files:**
- Modify: `src/app/core/realtime/room-socket.service.ts` (et ses types)
- Modify: `src/app/core/realtime/room-socket.service.spec.ts`
- Modify: `src/app/features/room/room.component.ts`
- Modify: `public/i18n/{fr,nl,en,it,es}.json`

**Interfaces:**
- Consomme : `vote.revealed` porte `tally: [{cardValue, count}]` et `spread`. La clé `votes` n'existe plus.

**Exigence structurante :** retirer de l'état client **toute** structure conservant un lien
participant → carte. Il ne suffit pas de cesser de l'afficher : si le réducteur la stocke encore, la
donnée reste lisible dans l'état de l'application. Elle ne doit plus exister nulle part côté client.

- [ ] **Step 1 : écrire le test qui échoue**

`room-socket.service.spec.ts` teste déjà le réducteur par instanciation directe, sans `TestBed` : c'est
le patron à suivre. Le test doit vérifier qu'après réception d'un `vote.revealed`, l'état exposé
contient bien le décompte, et qu'aucun identifiant de participant n'y est associé à une carte.

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `npm test`
Expected: FAIL

- [ ] **Step 3 : adapter le réducteur et l'affichage**

Remplacer le rendu des votes nominatifs par le décompte : une ligne par valeur ayant au moins une voix,
avec le nombre de voix. Réutiliser le **libellé traduit de la carte** (issu du `deckSnapshot`, comme
ailleurs dans l'application) plutôt que la valeur brute. Conserver l'affichage de l'écart (`spread`).

Supprimer du type d'état et du réducteur les champs qui portaient les votes nominatifs.

- [ ] **Step 4 : clés i18n dans les cinq catalogues**

| Clé | fr | en | nl | it | es |
|---|---|---|---|---|---|
| `room.reveal.votes_count` | {{count}} voix | {{count}} votes | {{count}} stemmen | {{count}} voti | {{count}} votos |
| `room.reveal.anonymous_hint` | Les votes sont anonymes | Votes are anonymous | Stemmen zijn anoniem | I voti sono anonimi | Los votos son anónimos |

- [ ] **Step 5 : tests et build**

Run: `npm test`
Expected: PASS, parité i18n incluse

Run: `npm run build`
Expected: succès

- [ ] **Step 6 : commit**

```bash
git add src/app/core/realtime/ src/app/features/room/ public/i18n/
git commit -m "feat(vote): affichage anonyme du decompte a la revelation"
```

---

## Vérification finale

- [ ] `.venv/Scripts/python -m pytest -q` vert dans `Poker_server`
- [ ] `npm test && npm run build` verts dans `Poker_frontend`
- [ ] **Contrôle d'anonymat réel** : à deux navigateurs, voter différemment, révéler, puis ouvrir
  l'onglet réseau et inspecter la trame `vote.revealed`. Elle ne doit contenir **aucun** identifiant de
  participant. C'est la seule vérification qui prouve l'anonymat ; l'absence d'affichage ne prouve rien.
