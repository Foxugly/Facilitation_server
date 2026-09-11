# Livraison 5b — `Response`, payload JSON, réponses par item

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer `Vote` (une valeur de carte par round) par `Response` (un payload JSON par **item**), pour qu'un round de N items recueille N réponses par participant — sans qu'un joueur de Delegation Poker voie la différence.

**Architecture:** Même stratégie qu'en 5a — schéma additif, transvasement, bascule du code, suppression. La différence tient à ce que **5b casse le contrat WebSocket** : `vote.cast {cardValue}` devient `response.cast {itemId, payload}`. Le back accepte donc les deux formes pendant toute la livraison, le front bascule dessus, et un dernier commit retire les alias — ceux de 5a compris. Trois déploiements en séquence, jamais un seul.

**Tech Stack:** Django 5 + DRF + Channels (daphne), PostgreSQL en CI/prod, sqlite en dev, pytest + pytest-django + pytest-asyncio (`asyncio_mode = auto`). Front : Angular 21 (dépôt `Facilitation_frontend`), Playwright pour l'e2e.

**Spec:** `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` (§3 modèle, §5 contrat, §6 registre, §8 découpage)

## Global Constraints

- **`pytest` vert à chaque commit.** Référence d'entrée : **264 passed**.
- Commandes : `.\.venv\Scripts\python.exe -m pytest -q`, `.\.venv\Scripts\python.exe manage.py <cmd>` (Windows, venv Python 3.14).
- **Le poker doit rester jouable après chaque tâche**, y compris depuis le front **déployé en production**, qui envoie encore `vote.cast`. C'est la contrainte qui commande tout l'ordonnancement.
- **Aucun message de commit accentué** ; commentaires de code en français sans accents.
- **Tests async du consumer : jamais d'`asyncio.sleep()` pour attendre un fait.** La barrière est un aller-retour `ping`/`pong`.
- Le mot « Session » reste banni du domaine, sauf le type de message `session.join`.
- **Les valeurs de réponse restent secrètes jusqu'au reveal.** L'invariant tient aujourd'hui parce que `revealed_payload` n'émet aucune clé `votes` sur un round anonyme : le préserver **par construction**, jamais par un masquage côté client.
- **Valider sur PostgreSQL avant tout push** — ici, la CI (PG 16) est le juge : la suite locale est en sqlite.

## L'ordre de déploiement, qui n'est pas négociable

| # | Ce qui part | Ce que le front de prod envoie | Ce que le back accepte |
|---|---|---|---|
| 1 | Tâches 1 à 4 (back) | `vote.cast {cardValue}` | `vote.cast` **et** `response.cast` |
| 2 | Tâche 5 (front) | `response.cast {itemId, payload}` | les deux |
| 3 | Tâche 6 (back) | `response.cast` | `response.cast` seul |

Inverser 2 et 3 casse toutes les salles vivantes. La tâche 6 ne part **qu'après** que le front de la tâche 5 est déployé et vérifié.

## Structure des fichiers

| Fichier | Responsabilité | Tâche |
|---|---|---|
| `rooms/models.py` | `Vote` → `Response`, `item`, `payload` | 1, 6 |
| `rooms/migrations/0013_response.py` | Renommage + champs additifs | 1 |
| `rooms/migration_ops.py` | `votes_to_responses(apps)` | 2 |
| `rooms/migrations/0014_votes_to_responses.py` | `RunPython` | 2 |
| `rooms/migrations/0015_drop_card_value.py` | Suppression de `card_value`, bascule de contrainte | 6 |
| `realtime/activities.py` | `payload_schema` + agrégateur déclarés par le registre | 3 |
| `realtime/services.py` | Écriture/lecture des réponses, agrégation par item | 3 |
| `realtime/consumers.py` | `response.cast` + alias `vote.cast` | 4 |
| `Facilitation_frontend` (autre dépôt) | protocole, service de socket, composants, e2e | 5 |
| `docs/superpowers/specs/delegation-poker-realtime-contract.md` | Contrat étendu puis nettoyé | 4, 6 |

---

### Task 1: `Response` — renommage et champs additifs

**Files:**
- Modify: `rooms/models.py` (`Vote` → `Response`, `item` et `payload` ajoutés, `card_value` rendu nullable)
- Create: `rooms/migrations/0013_response.py`
- Test: `rooms/tests/test_responses.py`

**Interfaces:**
- Consumes: `Item` (5a).
- Produces: `rooms.models.Response(round, participant, item, payload, card_value, created_at, updated_at)`, `related_name="responses"` sur `Round` **et** sur `Item`. Contrainte `(round, participant)` **inchangée à ce stade** — sa bascule vers `(item, participant)` est la tâche 6, une fois les données transvasées.

- [ ] **Step 1: Écrire les tests du modèle**

Créer `rooms/tests/test_responses.py` :

```python
"""La reponse est la contribution d'un participant a un ITEM (design §3).

`payload` est un JSON valide par le schema que declare le type d'activite : une
seule table pour toutes les activites, jamais une table par activite.
"""
import pytest

from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Response, Room, Round, RoundState
from rooms.snapshot import build_deck_snapshot


def _round_with_item(deck):
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=deck.vote_type,
        deck_snapshot=build_deck_snapshot(deck),
    )
    room.touch(save=False)
    room.save()
    rnd = Round.objects.create(room=room, state=RoundState.OPEN)
    item = Item.objects.create(round=rnd, text="Budget ?", sequence=1)
    return room, rnd, item


@pytest.mark.django_db
def test_response_carries_a_json_payload(standard_deck):
    room, rnd, item = _round_with_item(standard_deck)
    p = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")

    r = Response.objects.create(round=rnd, participant=p, item=item, payload={"card": "4"})
    r.refresh_from_db()

    assert r.payload == {"card": "4"}
    assert r.item_id == item.id


@pytest.mark.django_db
def test_responses_die_with_their_item(standard_deck):
    """Retirer un item emporte les reponses qui le visaient : elles n'ont plus
    d'objet, et les laisser orphelines fausserait tout decompte ulterieur."""
    room, rnd, item = _round_with_item(standard_deck)
    p = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")
    Response.objects.create(round=rnd, participant=p, item=item, payload={"card": "4"})

    item.delete()

    assert Response.objects.count() == 0


@pytest.mark.django_db
def test_payload_defaults_to_an_empty_dict(standard_deck):
    room, rnd, item = _round_with_item(standard_deck)
    p = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")

    r = Response.objects.create(round=rnd, participant=p, item=item)

    assert r.payload == {}
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `.\.venv\Scripts\python.exe -m pytest rooms/tests/test_responses.py -q`
Expected: FAIL — `ImportError: cannot import name 'Response' from 'rooms.models'`

- [ ] **Step 3: Renommer le modèle et ajouter les champs**

Dans `rooms/models.py`, renommer `class Vote` en `class Response`, corriger sa docstring, et :

```python
    # L'item auquel cette reponse repond. Nullable le temps du transvasement
    # (migration 0014) ; passe non-null en 0015.
    item = models.ForeignKey(
        "rooms.Item", on_delete=models.CASCADE, related_name="responses", null=True, blank=True
    )
    # La contribution elle-meme, validee par le schema que declare le type
    # d'activite (design §3, §6). UNE table pour toutes les activites : le poker
    # y ecrit {"card": "<valeur>"}, un dot voting y ecrira {"dots": 3}.
    payload = models.JSONField(default=dict, blank=True)
    # Ancienne forme, conservee le temps de la bascule du front. Supprimee en 0015.
    card_value = models.CharField(max_length=32, blank=True, default="")
```

`related_name` sur le round : `responses`. Garder la contrainte `uniq_vote_round_participant` telle quelle — la renommer ferait une migration de plus sans rien apporter avant la tâche 6.

- [ ] **Step 4: Générer la migration**

Run: `.\.venv\Scripts\python.exe manage.py makemigrations rooms -n response`
Expected: `RenameModel(old_name="Vote", new_name="Response")`, `AddField` ×2, `AlterField` sur `card_value`, `AlterField`/`RenameIndex` éventuels sur la contrainte.

⚠️ **Relire impérativement** : Django propose parfois `DeleteModel` + `CreateModel` au lieu d'un `RenameModel` — ce qui **détruirait tous les votes en base**. Si c'est le cas, écrire le `RenameModel` à la main.

- [ ] **Step 5: Lancer les tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: 267 passed (264 + 3). Les tests existants qui importent `Vote` doivent être mis à jour vers `Response` — c'est un renommage mécanique, pas une réécriture.

- [ ] **Step 6: Commit**

```bash
git add rooms/models.py rooms/migrations/0013_response.py rooms/tests/test_responses.py
git commit -m "Response : le vote devient une reponse a un item"
```

---

### Task 2: Transvaser les votes en réponses

**Files:**
- Modify: `rooms/migration_ops.py`
- Create: `rooms/migrations/0014_votes_to_responses.py`
- Test: `rooms/tests/test_migration_responses.py`

**Interfaces:**
- Consumes: `Response.item`, `Response.payload` (tâche 1).
- Produces: `rooms.migration_ops.votes_to_responses(apps)`. Après elle : toute `Response` a un `item` (celui de son round) et un `payload` `{"card": <card_value>}`.

- [ ] **Step 1: Écrire le test**

Créer `rooms/tests/test_migration_responses.py`, sur le modèle de `test_migration_items.py` (même fixture `head`, même `_migrate`, mêmes bornes `BEFORE = [("rooms", "0013_response")]` / `AFTER = [("rooms", "0014_votes_to_responses")]`). Il doit couvrir :

```python
def test_vote_becomes_a_response_to_the_round_item(head):
    """Un vote existant vise desormais l'item de son round, et sa valeur de carte
    devient un payload — la forme que toutes les activites partageront."""
    old = _migrate(BEFORE)
    _seed(old)          # une room, un round, un item, un participant, un vote "4"

    new = _migrate(AFTER)
    Response = new.get_model("rooms", "Response")

    r = Response.objects.get()
    assert r.payload == {"card": "4"}
    assert r.item.text == "Budget ?"
    assert r.card_value == "4"   # conserve : le front ne bascule qu'en tache 5


def test_a_round_without_item_leaves_its_responses_untouched(head):
    """Cas impossible aujourd'hui (tout round porte un item depuis 0011), mais la
    migration ne doit pas planter dessus : elle laisse la reponse en l'etat plutot
    que d'inventer un item."""
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `.\.venv\Scripts\python.exe -m pytest rooms/tests/test_migration_responses.py -q`
Expected: FAIL — `NodeNotFoundError: Migration rooms.0014_votes_to_responses not found`

- [ ] **Step 3: Écrire la fonction**

Dans `rooms/migration_ops.py` :

```python
def votes_to_responses(apps):
    """Rattache chaque reponse a l'item de son round et transpose sa valeur de
    carte en payload (design §3).

    Le payload est la forme commune a toutes les activites : le poker y ecrit
    {"card": "<valeur>"}, une activite a venir y ecrira sa propre structure. La
    colonne `card_value` reste remplie jusqu'en 0015 — le front de production
    l'attend encore au moment ou cette migration tourne.
    """
    Response = apps.get_model("rooms", "Response")
    Item = apps.get_model("rooms", "Item")

    for response in Response.objects.all().select_related("round"):
        item = Item.objects.filter(round_id=response.round_id).order_by("sequence", "id").first()
        if item is None:
            # Aucun item : impossible depuis 0011, mais inventer un item ici
            # fabriquerait un sujet que personne n'a jamais pose.
            continue
        response.item = item
        response.payload = {"card": response.card_value}
        response.save(update_fields=["item", "payload"])
```

- [ ] **Step 4: Écrire la migration**

`rooms/migrations/0014_votes_to_responses.py`, dépendance `("rooms", "0013_response")`, `RunPython(forwards, migrations.RunPython.noop)`, docstring disant que le sens arrière est un `noop` (les colonnes retombent avec 0013).

- [ ] **Step 5: Lancer les tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: 269 passed.

- [ ] **Step 6: Commit**

```bash
git add rooms/migration_ops.py rooms/migrations/0014_votes_to_responses.py rooms/tests/test_migration_responses.py
git commit -m "Transvase les votes en reponses a un item"
```

---

### Task 3: Le domaine agrège par item, le registre déclare le schéma

**Files:**
- Modify: `realtime/activities.py` (`payload_schema`, `aggregate`)
- Modify: `realtime/services.py` (`cast_vote` → `cast_response`, `revealed_payload`, `participation`, `participants_list`, `build_state_sync`, `reveal`)
- Test: `realtime/tests/test_responses_services.py`

**Interfaces:**
- Consumes: `Response` (tâches 1-2), `ACTIVITY_REGISTRY` (livraison 4).
- Produces, dans `realtime.services` :
  - `cast_response(room, participant, item_id, payload) -> dict`
  - `responses_of(rnd, item) -> list[Response]`
  - `revealed_payload(room)` — gagne une clé `items: [{itemId, tally, spread, votes?}]` **en gardant** ses clés plates actuelles (`tally`, `spread`, `votes`), calculées sur le **premier item**, le temps que le front bascule.
  - `build_state_sync` — gagne `myResponses: {"<itemId>": payload}` **en gardant** `myVote`.
- Dans `realtime.activities`, `ActivitySpec` gagne :
  - `payload_schema: dict` — les clés attendues et leur type.
  - `aggregate: Callable[[list[Response], list[str]], dict]` — le décompte d'un item.

- [ ] **Step 1: Écrire les tests**

Créer `realtime/tests/test_responses_services.py`. Il doit couvrir, chacun avec une assertion sur une valeur concrète :

1. `cast_response` écrit un payload sur l'item visé et **refuse** un `itemId` d'un autre round (`RoomError`, `rejected_type == "response.cast"`).
2. Un même participant répond à **deux items** du même round : deux `Response`, aucune n'écrase l'autre. *(C'est l'objet même de la livraison — sans ce test, rien ne prouve que la contrainte a bougé.)*
3. `cast_response` refuse une valeur de carte hors du deck, comme `cast_vote` le faisait, et refuse un payload dont les clés ne correspondent pas au `payload_schema` du registre.
4. `revealed_payload` porte un décompte **par item**, et ses clés plates restent celles du premier item.
5. **L'invariant d'anonymat tient par item** : sur un round anonyme, aucune entrée de `items[]` ne porte de clé `votes`. *À vérifier par mutation : en retirant la garde, le test doit échouer.*
6. `build_state_sync` porte `myResponses` **et** `myVote`.

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `.\.venv\Scripts\python.exe -m pytest realtime/tests/test_responses_services.py -q`
Expected: FAIL — `AttributeError: module 'realtime.services' has no attribute 'cast_response'`

- [ ] **Step 3: Étendre le registre**

Dans `realtime/activities.py`, ajouter à `ActivitySpec` :

```python
    #: Les cles attendues dans `Response.payload`, et leur type. Le domaine les
    #: valide AVANT d'ecrire : une activite qui ajoute une cle ne touche que ce
    #: fichier, jamais `services.cast_response`.
    payload_schema: dict[str, type] = field(default_factory=lambda: {"card": str})
```

plus l'agrégateur, que le design §6 met au même endroit :

```python
    #: Responses d'un item -> decompte. Vit ici et non dans `services` pour que
    #: l'objectif tienne : ajouter une activite ne doit toucher que ce fichier.
    #: Signature : (responses, card_values) -> {"tally": [...], "spread": {...}}
    aggregate: Callable[[list, list[str]], dict] = field(default=None)
```

et une fonction `validate_payload(strategy, payload)` qui lève `RoomError("state.invalid_transition", ..., "response.cast")` si une clé manque, est en trop, ou n'a pas le bon type. `revealed_payload` appelle l'agrégateur du registre au lieu de compter lui-même ; la stratégie par défaut garde le comptage actuel, de sorte qu'aucune activité existante ne change de dépouillement.

- [ ] **Step 4: Réécrire le domaine**

`cast_response(room, participant, item_id, payload)` reprend les gardes de `cast_vote` (round ouvert, échéance non dépassée, valeur dans le deck) et y ajoute : l'item doit appartenir au round courant, et le payload doit passer `validate_payload`. Elle écrit `payload` **et** `card_value` (le temps de la fenêtre), avec `update_or_create(item=item, participant=participant, ...)`.

`cast_vote(room, participant, card_value)` devient une **façade** qui résout le premier item du round courant et délègue à `cast_response` — un seul chemin de code, donc un seul endroit où les règles vivent.

`revealed_payload` calcule un bloc par item (décompte, écart via `_spread_for`, et `votes` **seulement** si le round n'est pas anonyme), puis recopie le bloc du premier item dans les clés plates.

`participation`, `participants_list` et `reveal` comptent désormais « a répondu à **tous** les items du round » plutôt que « a voté » : un participant à mi-chemin n'est pas un participant qui a fini. `reveal` refuse toujours si aucune réponse n'existe.

- [ ] **Step 5: Lancer la suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: 275 passed. Les tests existants du poker doivent passer **sans modification** : ils envoient `vote.cast`, que la façade traduit.

- [ ] **Step 6: Commit**

```bash
git add realtime/activities.py realtime/services.py realtime/tests/test_responses_services.py
git commit -m "Le domaine agrege par item, le registre declare le payload"
```

---

### Task 4: `response.cast` dans le contrat, `vote.cast` en alias

**Files:**
- Modify: `realtime/consumers.py`
- Modify: `docs/superpowers/specs/delegation-poker-realtime-contract.md`
- Test: `realtime/tests/test_responses_ws.py`

**Interfaces:**
- Produces (entrant) : `response.cast {itemId, payload}` — **tous les participants**, pas seulement le facilitateur.
- Produces (sortant) : `vote.revealed` gagne `items: [...]` en gardant ses clés plates.
- `vote.cast {cardValue}` reste accepté et **produit exactement les mêmes diffusions qu'aujourd'hui**.

- [ ] **Step 1: Écrire les tests**

Créer `realtime/tests/test_responses_ws.py` : `response.cast` sur deux items du même round met à jour la participation sans que la seconde réponse écrase la première ; `vote.cast` continue de fonctionner **à l'identique** (même diffusion `participation.update`) ; `vote.revealed` porte `items[]` et les clés plates ; un `response.cast` visant un item d'un autre round reçoit une `error` typée. Barrière `ping`/`pong`, jamais `asyncio.sleep`.

- [ ] **Step 2: Lancer, vérifier l'échec** — `Unknown type response.cast`.

- [ ] **Step 3: Brancher** la nouvelle intention dans `_dispatch`, juste avant l'alias `vote.cast`, et étendre la section 8.1 du contrat (ne renuméroter aucune section existante) avec une note datée : « `vote.cast` et les clés plates de `vote.revealed` sont des alias hérités, supprimés à la fin de 5b ».

- [ ] **Step 4: Lancer la suite** — Expected: 279 passed.

- [ ] **Step 5: Commit**

```bash
git commit -m "Contrat : response.cast, vote.cast devient un alias"
```

- [ ] **Step 6: Déployer le back et vérifier le poker en production**

Pousser sur `main`, attendre la CI (PostgreSQL 16) et le déploiement, puis **jouer une partie complète depuis le front de production non modifié**. C'est la seule preuve que la façade tient. Ne pas enchaîner sur la tâche 5 avant ce contrôle.

---

### Task 5: Le front bascule (dépôt `Facilitation_frontend`)

**Files:** `src/app/core/realtime/protocol.ts`, `room-socket.service.ts`, les composants de `features/room/activities/delegation-poker/`, les specs Playwright de `e2e/`.

**Interfaces:**
- Consumes: `response.cast`, `myResponses`, `vote.revealed.items[]` (tâches 3-4).
- Produces: un front qui n'envoie plus jamais `vote.cast` ni ne lit `myVote`.

- [ ] **Step 1** — `protocol.ts` : `ResponseCast {itemId, payload}`, `StateSync.myResponses`, `Revealed.items[]`. Garder les anciens types **marqués dépréciés**, pour que le compilateur signale ce qui reste à migrer.
- [ ] **Step 2** — `RoomSocketService` : émettre `response.cast`, lire `myResponses` et `items[]`. L'état d'activité (réponses, décompte) se sépare de l'état de room, conformément à la cible §Frontend.
- [ ] **Step 3** — Composants : la main et le tapis lisent la réponse de l'item courant plutôt que `myVote`.
- [ ] **Step 4** — e2e : les quatre specs passent, **inchangées dans leur intention**. Si l'une doit bouger, dire ce que la nouvelle version vérifie face à l'ancienne.
- [ ] **Step 5** — `npm test` et `npx playwright test` verts, puis commit, PR, déploiement, et **vérification d'une partie réelle**.

---

### Task 6: Retirer les alias — 5a et 5b

**Files:**
- Modify: `rooms/models.py`, `realtime/services.py`, `realtime/consumers.py`
- Create: `rooms/migrations/0015_drop_card_value.py`
- Modify: `docs/superpowers/specs/delegation-poker-realtime-contract.md`

**⚠️ Cette tâche ne démarre qu'une fois la tâche 5 déployée et vérifiée en production.** Elle casse tout client resté sur l'ancienne forme.

- [ ] **Step 1** — Retirer du consumer : `vote.cast`, `subject.set`, `subject.add`, `subject.select` (alias de 5a). Retirer de `services` : la façade `cast_vote`, `set_subject`, `add_subject`, `select_subject`, `current_subject_text`.
- [ ] **Step 2** — Retirer des payloads : `myVote` de `state.sync`, les clés plates de `vote.revealed`, `Response.card_value`.
- [ ] **Step 3** — Migration `0015` : `Response.item` passe non-null, la contrainte `(round, participant)` cède la place à `(item, participant)`, `card_value` est supprimée. **Documenter l'irréversibilité** sur base peuplée, comme `0012`.
- [ ] **Step 4** — Contrat : supprimer les notes d'alias devenues fausses, plutôt que de les laisser mentir.
- [ ] **Step 5** — Suite complète verte, e2e front verts, puis déploiement.

---

### Task 7: Documentation

- [ ] `CLAUDE.md` : `Response` passe de « à renommer depuis `Vote` » à **existe** ; l'étape 5b est faite ; la référence de la suite est mise à jour ; les écarts connus perdent ce qui a cessé d'être un écart.
- [ ] `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` : cocher 5b dans le tableau §8.
- [ ] Commit.

---

## Vérification finale

- [ ] `pytest` vert en local (sqlite) et en CI (PostgreSQL 16).
- [ ] Un round à **deux items** joué de bout en bout : chaque participant répond aux deux, le dépouillement montre deux décomptes, et l'historique porte deux résultats.
- [ ] Un round anonyme à deux items : **aucune** entrée de `items[]` ne porte de clé `votes`. Vérifié par mutation.
- [ ] Une partie de Delegation Poker ordinaire, jouée depuis le front de production, identique à avant.
- [ ] `git grep -n "card_value\|myVote\|vote.cast" -- "*.py"` ne renvoie plus rien hors `migrations/`.
