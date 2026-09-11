# Livraison 5a — `Item` rattaché au round

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer `Subject` (porté par la room, un par round) par `Item` (porté par le round, N par round), sans changer une seule chose visible pour un joueur de Delegation Poker.

**Architecture:** Migration en trois temps — on ajoute `Item` à côté de `Subject` (schéma additif), on transvase les données, puis on supprime `Subject` une fois que plus personne ne le lit. Entre les deux, les services et le consumer basculent sur les items. Le contrat WebSocket gagne `item.add/update/remove/reorder` et `round.select`, mais **continue d'accepter et d'émettre les anciens types** (`subject.*`, `agenda.updated`) : `Facilitation_frontend` n'a rien à reprendre dans cette livraison. Les alias meurent en 5b.

**Tech Stack:** Django 5 + DRF + Channels (daphne), PostgreSQL en CI/prod, sqlite en dev, pytest + pytest-django + pytest-asyncio (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-09-11-scenario-et-items-design.md` (§3 modèle, §4 migration, §5 contrat, §8 découpage)

## Global Constraints

- **`pytest` vert à chaque commit.** Référence d'entrée : **240 passed**. Un commit qui baisse ce chiffre sans supprimer délibérément un test est un échec.
- Commandes : `.\.venv\Scripts\python.exe -m pytest`, `.\.venv\Scripts\python.exe manage.py <cmd>` (Windows, venv Python 3.14).
- **Le poker doit rester jouable après chaque tâche.** Aucune tâche ne livre une régression « qu'on corrigera après ».
- **Aucun message de commit accentué** (convention du dépôt : « Registre d'activites cote serveur »).
- **Tests async du consumer : jamais d'`asyncio.sleep()` pour attendre un fait.** La barrière est un aller-retour, le consumer traitant les messages d'une connexion en série :
  ```python
  await comm.send_json_to({"v": 1, "type": "ping", "payload": {}})
  await _drain_until(comm, "pong")
  ```
- **Le mot « Session » reste banni du domaine.** Seule exception : le type de message `session.join`.
- **Valider sur PostgreSQL avant tout push** (tâche 7). sqlite laisse passer des violations NOT NULL / unique.
- `Item.author` et `Item.origin_item` sont créés dans cette livraison mais **ne sont écrits par personne** avant 5c / 5e. C'est voulu : le champ existe, la migration ne se rejoue pas.

## Structure des fichiers

| Fichier | Responsabilité | Tâche |
|---|---|---|
| `rooms/models.py` | `Item` ajouté, `Round.subject` rendu nullable, `Result.item` ajouté | 1, 6 |
| `rooms/migrations/0010_item.py` | Schéma additif | 1 |
| `rooms/migration_ops.py` | **Créé.** La logique de transvasement, testable hors migration | 2 |
| `rooms/migrations/0011_subjects_to_items.py` | `RunPython` appelant `migration_ops` | 2 |
| `rooms/migrations/0012_drop_subject.py` | Suppression de `Subject` et des FK mortes | 6 |
| `realtime/services.py` | Domaine : items du round courant, agenda sur les rounds, `state.sync` | 3 |
| `realtime/consumers.py` | Nouveaux types `item.*` / `round.select` + alias hérités | 4 |
| `history/api_views.py` | `Result.subject` → `Result.item` | 5 |
| `rooms/tests/test_items.py` | **Créé.** Le modèle | 1 |
| `rooms/tests/test_migration_items.py` | **Créé.** Les trois cas de migration | 2 |
| `realtime/tests/test_items_services.py` | **Créé.** Le domaine, sans socket | 3 |
| `realtime/tests/test_items_ws.py` | **Créé.** Le contrat, nouveaux types et alias | 4 |
| `docs/superpowers/specs/delegation-poker-realtime-contract.md` | Le contrat, étendu | 7 |

---

### Task 1: Modèle `Item` + migration additive

**Files:**
- Modify: `rooms/models.py` (`Subject` inchangé ; `Round.subject` → `null=True` ; `Item` ajouté ; `Result.item` ajouté)
- Create: `rooms/migrations/0010_item.py` (généré)
- Test: `rooms/tests/test_items.py`

**Interfaces:**
- Consumes: rien.
- Produces: `rooms.models.Item(round, text, sequence, author, origin_item, created_at)`, `related_name="items"` sur le round ; `Result.item` (FK `Item`, `null=True` à ce stade).

- [ ] **Step 1: Écrire les tests du modèle**

Créer `rooms/tests/test_items.py` :

```python
"""L'item est l'unite qu'une activite manipule (design 2026-09-11 §3).

Porte par le ROUND et non par la room : un round est une activite jouee sur N
items, et un item copie d'un round a l'autre ne doit pas se reecrire a la source.
"""
import pytest

from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Round, RoundState, Room
from rooms.snapshot import build_deck_snapshot


def _room(deck):
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=deck.vote_type,
        deck_snapshot=build_deck_snapshot(deck),
    )
    room.touch(save=False)
    room.save()
    return room


@pytest.mark.django_db
def test_items_are_ordered_by_sequence(standard_deck):
    room = _room(standard_deck)
    rnd = Round.objects.create(room=room, state=RoundState.IDLE)
    Item.objects.create(round=rnd, text="B", sequence=2)
    Item.objects.create(round=rnd, text="A", sequence=1)

    assert [i.text for i in rnd.items.all()] == ["A", "B"]


@pytest.mark.django_db
def test_items_die_with_their_round(standard_deck):
    room = _room(standard_deck)
    rnd = Round.objects.create(room=room, state=RoundState.IDLE)
    Item.objects.create(round=rnd, text="A", sequence=1)

    rnd.delete()

    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_author_survives_the_participant_leaving(standard_deck):
    """Un post-it ne disparait pas parce que son auteur a quitte la salle : le
    lien se vide, l'idee reste (design §3)."""
    room = _room(standard_deck)
    rnd = Round.objects.create(room=room, state=RoundState.IDLE)
    author = Participant.objects.create(room=room, token=generate_token(), display_name="Alex")
    item = Item.objects.create(round=rnd, text="A", sequence=1, author=author)

    author.delete()
    item.refresh_from_db()

    assert item.text == "A"
    assert item.author_id is None
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `.\.venv\Scripts\python.exe -m pytest rooms/tests/test_items.py -q`
Expected: FAIL — `ImportError: cannot import name 'Item' from 'rooms.models'`

- [ ] **Step 3: Ajouter le modèle**

Dans `rooms/models.py`, après `Subject` :

```python
class Item(models.Model):
    """Un sujet manipule par une activite : un point d'agenda pose par le
    facilitateur, ou un post-it ecrit par un participant.

    Porte par le ROUND (design 2026-09-11 §3) : les items d'un round passe ne
    bougent plus, meme si le meme sujet est rejoue ou reformule plus tard.
    """

    round = models.ForeignKey("rooms.Round", on_delete=models.CASCADE, related_name="items")
    text = models.CharField(max_length=300)
    sequence = models.PositiveSmallIntegerField(default=1)
    # Qui l'a ecrit. Null quand le facilitateur pose un sujet au nom de la salle.
    # L'anonymat d'un brainstorming est une politique d'AFFICHAGE cote serveur
    # (on n'emet pas la cle), jamais un champ vide en base — meme regle que
    # Vote.participant.
    author = models.ForeignKey(
        Participant, on_delete=models.SET_NULL, null=True, blank=True, related_name="items"
    )
    # L'item dont celui-ci est la copie, quand une activite reprend la sortie de
    # la precedente (chainage, livraison 5e). Copie et NON reference : reformuler
    # ici ne doit pas reecrire l'historique de l'activite source.
    origin_item = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="copies"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("round", "sequence", "id")

    def __str__(self):
        return self.text
```

Puis, dans `Round`, rendre l'ancien lien facultatif — la migration de données le remplira encore, la tâche 6 le supprimera :

```python
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="rounds", null=True, blank=True
    )
```

Et dans `Result`, ajouter le nouveau lien à côté de l'ancien :

```python
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="results", null=True, blank=True)
```

- [ ] **Step 4: Générer la migration**

Run: `.\.venv\Scripts\python.exe manage.py makemigrations rooms -n item`
Expected: `rooms/migrations/0010_item.py` — création de `Item`, `AlterField` sur `Round.subject`, `AddField` `Result.item`. **Relire le fichier** : aucune suppression ne doit y figurer.

- [ ] **Step 5: Lancer les tests**

Run: `.\.venv\Scripts\python.exe -m pytest rooms/tests/test_items.py -q && .\.venv\Scripts\python.exe -m pytest -q`
Expected: les 3 nouveaux PASS, et le total passe de 240 à 243.

- [ ] **Step 6: Commit**

```bash
git add rooms/models.py rooms/migrations/0010_item.py rooms/tests/test_items.py
git commit -m "Item : le sujet passe du cote du round"
```

---

### Task 2: Migration de données `Subject` → `Item`

**Files:**
- Create: `rooms/migration_ops.py`
- Create: `rooms/migrations/0011_subjects_to_items.py`
- Test: `rooms/tests/test_migration_items.py`

**Interfaces:**
- Consumes: `Item`, `Result.item` (tâche 1).
- Produces: `rooms.migration_ops.subjects_to_items(apps)` — transvase, ne retourne rien. Après elle : tout `Round` a ≥ 1 `Item`, tout `Result` a un `item`, tout `Subject` orphelin a gagné un `Round` `idle`.

- [ ] **Step 1: Écrire le test des trois cas**

Créer `rooms/tests/test_migration_items.py` :

```python
"""Les trois cas de transvasement Subject -> Item (design 2026-09-11 §4).

Le test joue la VRAIE migration via MigrationExecutor plutot que d'appeler la
fonction sur les modeles courants : c'est la seule facon de verifier ce qui
tournera en prod, sur le moteur qui compte. Il exige transaction=True (la
migration fait du DDL) et remet la base a l'etat de tete en sortie, sans quoi
tous les tests suivants s'executeraient sur un schema perime.
"""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("rooms", "0010_item")]
AFTER = [("rooms", "0011_subjects_to_items")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


@pytest.fixture
def head():
    """Remet la base a l'etat de tete quoi qu'il arrive."""
    yield
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _seed(apps):
    """Un jeu couvrant les trois cas, ecrit avec les modeles HISTORIQUES."""
    VoteType = apps.get_model("decks", "VoteType")
    Room = apps.get_model("rooms", "Room")
    Subject = apps.get_model("rooms", "Subject")
    Round = apps.get_model("rooms", "Round")
    Result = apps.get_model("rooms", "Result")

    vt = VoteType.objects.create(code="delegation_poker", resolution_strategy="delegation_v1")
    room = Room.objects.create(
        code="AAAA1111",
        vote_type=vt,
        deck_snapshot={"cards": []},
        expires_at=timezone.now() + timezone.timedelta(hours=8),
    )
    # Cas 1 : un subject, un round joue et acte.
    played = Subject.objects.create(room=room, text="Budget ?", sequence=1)
    rnd = Round.objects.create(room=room, subject=played, state="acted")
    Result.objects.create(round=rnd, subject=played, chosen_value="4")
    # Cas 2 : un subject rejoue — deux rounds sur le meme sujet.
    revoted = Subject.objects.create(room=room, text="Embauche ?", sequence=2)
    Round.objects.create(room=room, subject=revoted, state="acted")
    Round.objects.create(room=room, subject=revoted, state="idle")
    # Cas 3 : un subject prepare, jamais joue.
    Subject.objects.create(room=room, text="Congés ?", sequence=3)
    return room.code


def test_played_subject_becomes_the_single_item_of_its_round(head):
    old = _migrate(BEFORE)
    _seed(old)

    new = _migrate(AFTER)
    Round = new.get_model("rooms", "Round")
    Result = new.get_model("rooms", "Result")

    rnd = Round.objects.get(state="acted", items__text="Budget ?")
    assert [i.text for i in rnd.items.all()] == ["Budget ?"]
    assert Result.objects.get(round=rnd).item.text == "Budget ?"


def test_revoted_subject_gets_one_item_copy_per_round(head):
    old = _migrate(BEFORE)
    _seed(old)

    new = _migrate(AFTER)
    Item = new.get_model("rooms", "Item")

    copies = list(Item.objects.filter(text="Embauche ?").order_by("id"))
    assert len(copies) == 2
    assert copies[0].origin_item_id is None
    # La seconde est marquee comme copie de la premiere : reformuler le round
    # rejoue ne doit pas reecrire ce qui a ete decide au premier tour.
    assert copies[1].origin_item_id == copies[0].id


def test_orphan_subject_becomes_a_prepared_round(head):
    """Un sujet prepare mais jamais joue devient un round idle qui le porte :
    c'est exactement le scenario prepare vise par le design §1."""
    old = _migrate(BEFORE)
    _seed(old)

    new = _migrate(AFTER)
    Item = new.get_model("rooms", "Item")

    item = Item.objects.get(text="Congés ?")
    assert item.round.state == "idle"
    assert item.round.items.count() == 1
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `.\.venv\Scripts\python.exe -m pytest rooms/tests/test_migration_items.py -q`
Expected: FAIL — `NodeNotFoundError: Migration rooms.0011_subjects_to_items not found`

- [ ] **Step 3: Écrire la logique de transvasement**

Créer `rooms/migration_ops.py` :

```python
"""Operations de migration de donnees, sorties des fichiers de migration.

Les fichiers de `migrations/` sont figes une fois joues ; les garder minces et
mettre la logique ici la rend lisible et testable. Ces fonctions ne prennent que
le registre `apps` de la migration : elles n'importent AUCUN modele concret, sans
quoi elles casseraient des que le modele evoluerait.
"""


def subjects_to_items(apps):
    """Transvase `Subject` (porte par la room) vers `Item` (porte par le round).

    Trois cas (design 2026-09-11 §4) :

    - un subject joue une fois -> l'unique item de son round ;
    - un subject rejoue -> une COPIE d'item par round, `origin_item` pointant la
      premiere. Sans cela, reformuler le sujet du second tour reecrirait ce qui a
      ete decide au premier ;
    - un subject prepare mais jamais joue -> un round `idle` cree pour le porter.
      C'est le scenario prepare, tel qu'il existait deja sans le nom.
    """
    Subject = apps.get_model("rooms", "Subject")
    Round = apps.get_model("rooms", "Round")
    Item = apps.get_model("rooms", "Item")
    Result = apps.get_model("rooms", "Result")

    for subject in Subject.objects.all().order_by("room_id", "sequence", "id"):
        rounds = list(subject.rounds.all().order_by("created_at", "id"))
        if not rounds:
            rounds = [Round.objects.create(room_id=subject.room_id, subject=subject, state="idle")]
        origin = None
        for rnd in rounds:
            item = Item.objects.create(
                round=rnd, text=subject.text, sequence=1, origin_item=origin
            )
            origin = origin or item

    for result in Result.objects.all().select_related("round"):
        # Un round porte exactement un item a ce stade : celui du subject qu'il
        # jouait. `first()` sur l'ordre du modele suffit donc, et restera juste
        # quand un round en portera N (le resultat suivra son propre item).
        item = Item.objects.filter(round_id=result.round_id).order_by("sequence", "id").first()
        if item is not None:
            result.item = item
            result.save(update_fields=["item"])
```

- [ ] **Step 4: Écrire la migration**

Créer `rooms/migrations/0011_subjects_to_items.py` :

```python
from django.db import migrations

from rooms.migration_ops import subjects_to_items


def forwards(apps, schema_editor):
    subjects_to_items(apps)


class Migration(migrations.Migration):
    dependencies = [("rooms", "0010_item")]

    # Irreversible en pratique : revenir en arriere supposerait de deviner quels
    # rounds ont ete crees pour des subjects orphelins. Les items retombent de
    # toute facon avec la table, supprimee par 0010 en sens inverse.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
```

- [ ] **Step 5: Lancer les tests**

Run: `.\.venv\Scripts\python.exe -m pytest rooms/tests/test_migration_items.py -q`
Expected: 3 PASS.

Si `MigrationExecutor` se révèle instable sous pytest-django (erreur de connexion sur un test transactionnel), **ne pas supprimer le test** : le convertir en appel direct de `subjects_to_items(django.apps.apps)` sur des données créées avec les modèles courants (`Subject` existe encore jusqu'à la tâche 6), et noter dans le fichier que la version exécuteur a été tentée.

- [ ] **Step 6: Suite complète + commit**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: 246 passed.

```bash
git add rooms/migration_ops.py rooms/migrations/0011_subjects_to_items.py rooms/tests/test_migration_items.py
git commit -m "Transvase les sujets vers les items du round"
```

---

### Task 3: Le domaine bascule sur les items

**Files:**
- Modify: `realtime/services.py` (`set_subject`, `add_subject`, `select_subject`, `build_agenda`, `current_subject_text`, `prepare_round`, `act_result`, `build_state_sync`)
- Test: `realtime/tests/test_items_services.py`

**Interfaces:**
- Consumes: `Item` (tâche 1).
- Produces, dans `realtime.services` :
  - `items_payload(rnd) -> list[dict]` → `[{"id": int, "text": str, "sequence": int}]`
  - `add_item(room, participant, text) -> dict` (l'item ajouté, au round courant)
  - `update_item(room, participant, item_id, text) -> dict`
  - `remove_item(room, participant, item_id) -> int` (l'id retiré)
  - `reorder_items(room, participant, item_ids) -> list[dict]`
  - `select_round(room, participant, round_id) -> dict` → `{"roundId": int, "items": [...], "text": str}`
  - `set_current_item(room, participant, text) -> str` (ex-`set_subject`, même sémantique)
  - `add_scenario_item(room, participant, text) -> int` (ex-`add_subject`, retourne l'id du **round** créé)
  - `current_item_text(room) -> str` (ex-`current_subject_text`)
  - `build_agenda(room)` — inchangé de forme, `id` devient un **id de round**

- [ ] **Step 1: Écrire les tests du domaine**

Créer `realtime/tests/test_items_services.py` :

```python
"""Le domaine manipule des items de round (design 2026-09-11 §3, §5).

Tests synchrones : `services` est volontairement pauvre en framework, donc
testable sans socket.
"""
import pytest

from realtime import services
from realtime.services import RoomError
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, RoundState
from rooms.snapshot import build_deck_snapshot


@pytest.fixture
def room_with_facilitator(standard_deck):
    room = Room(
        code=generate_unique_code(lambda c: Room.objects.filter(code=c).exists()),
        vote_type=standard_deck.vote_type,
        deck_snapshot=build_deck_snapshot(standard_deck),
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(
        room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR
    )
    voter = Participant.objects.create(
        room=room, token=generate_token(), display_name="Alex", role=Role.VOTER
    )
    return room, fac, voter


@pytest.mark.django_db
def test_add_item_puts_n_items_on_the_same_round(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    services.add_item(room, fac, "Embauche ?")

    room.refresh_from_db()
    assert [i["text"] for i in services.items_payload(room.current_round)] == [
        "Budget ?",
        "Embauche ?",
    ]
    assert [i["sequence"] for i in services.items_payload(room.current_round)] == [1, 2]


@pytest.mark.django_db
def test_a_voter_may_not_add_an_item_to_a_poker_round(room_with_facilitator):
    """En 5a la creation d'items reste facilitateur seul. Le registre ouvrira la
    porte aux participants en 5c (`items_authored_by`)."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")

    with pytest.raises(RoomError) as exc:
        services.add_item(room, voter, "Mon post-it")

    assert exc.value.rejected_type == "item.add"


@pytest.mark.django_db
def test_update_and_remove_item(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    second = services.add_item(room, fac, "Embauche ?")

    services.update_item(room, fac, second["id"], "Embauche 2027 ?")
    room.refresh_from_db()
    assert services.items_payload(room.current_round)[1]["text"] == "Embauche 2027 ?"

    services.remove_item(room, fac, second["id"])
    room.refresh_from_db()
    assert [i["text"] for i in services.items_payload(room.current_round)] == ["Budget ?"]


@pytest.mark.django_db
def test_reorder_items_renumbers_the_sequence(room_with_facilitator):
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "A")
    b = services.add_item(room, fac, "B")
    room.refresh_from_db()
    a = services.items_payload(room.current_round)[0]

    out = services.reorder_items(room, fac, [b["id"], a["id"]])

    assert [i["text"] for i in out] == ["B", "A"]
    assert [i["sequence"] for i in out] == [1, 2]


@pytest.mark.django_db
def test_agenda_lists_rounds_and_select_round_resets_to_idle(room_with_facilitator):
    """L'agenda designe desormais des ROUNDS. Le front renvoie l'id qu'il a recu,
    donc l'alias `subject.select` continue de fonctionner sans le savoir."""
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    services.add_scenario_item(room, fac, "Embauche ?")

    agenda = services.build_agenda(room)
    assert [e["text"] for e in agenda] == ["Budget ?", "Embauche ?"]

    out = services.select_round(room, fac, agenda[1]["id"])
    room.refresh_from_db()
    assert out["text"] == "Embauche ?"
    assert room.current_round_id == agenda[1]["id"]
    assert room.current_round.state == RoundState.IDLE


@pytest.mark.django_db
def test_state_sync_carries_items_and_the_legacy_subject(room_with_facilitator):
    """Les deux formes cohabitent le temps que le front bascule (design §5)."""
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")

    state = services.build_state_sync(fac)

    assert state["subject"] == "Budget ?"
    assert [i["text"] for i in state["items"]] == ["Budget ?"]
    assert state["round"]["id"] == Room.objects.get(pk=room.pk).current_round_id
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `.\.venv\Scripts\python.exe -m pytest realtime/tests/test_items_services.py -q`
Expected: FAIL — `AttributeError: module 'realtime.services' has no attribute 'set_current_item'`

- [ ] **Step 3: Réécrire les fonctions de `services.py`**

Remplacer l'import `Subject` par `Item`, puis les fonctions concernées :

```python
def items_payload(rnd):
    """Les items d'un round, dans l'ordre du facilitateur."""
    if rnd is None:
        return []
    return [{"id": i.id, "text": i.text, "sequence": i.sequence} for i in rnd.items.all()]


def _new_round(room, participant, text):
    """Un round neuf portant un premier item. Le scenario est une file de rounds :
    poser un nouveau sujet, c'est ouvrir un round de plus."""
    rnd = Round.objects.create(room=room, state=RoundState.IDLE, facilitator=participant)
    Item.objects.create(round=rnd, text=text, sequence=1)
    return rnd


def _first_item(rnd):
    return rnd.items.first() if rnd else None


def set_current_item(room, participant, text):
    """Ex-`set_subject`, sémantique inchangée : on reecrit l'item du round courant
    s'il est encore idle, sinon on ouvre un round neuf."""
    _require_facilitator(room, participant, "item.update")
    rnd = _current_round(room)
    if rnd and rnd.state == RoundState.IDLE and _first_item(rnd):
        item = _first_item(rnd)
        item.text = text
        item.save(update_fields=["text"])
    else:
        rnd = _new_round(room, participant, text)
        room.current_round = rnd
        room.save(update_fields=["current_round"])
    room.touch()
    return text


def current_item_text(room):
    return _first_item(_current_round(room)).text if _first_item(_current_round(room)) else ""


def add_item(room, participant, text):
    """Ajoute un item AU ROUND COURANT — le N-items du design §3."""
    _require_facilitator(room, participant, "item.add")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.add")
    rnd = _current_round(room)
    if rnd is None:
        rnd = _new_round(room, participant, text)
        room.current_round = rnd
        room.save(update_fields=["current_round"])
        item = _first_item(rnd)
    else:
        seq = rnd.items.count() + 1
        item = Item.objects.create(round=rnd, text=text, sequence=seq)
    room.touch()
    return {"id": item.id, "text": item.text, "sequence": item.sequence}


def _item_of_room(room, item_id, rejected_type):
    item = Item.objects.filter(id=item_id, round__room=room).select_related("round").first()
    if item is None:
        raise RoomError("state.invalid_transition", "Unknown item", rejected_type)
    return item


def update_item(room, participant, item_id, text):
    _require_facilitator(room, participant, "item.update")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.update")
    item = _item_of_room(room, item_id, "item.update")
    item.text = text
    item.save(update_fields=["text"])
    room.touch()
    return {"id": item.id, "text": item.text, "sequence": item.sequence}


def remove_item(room, participant, item_id):
    _require_facilitator(room, participant, "item.remove")
    item = _item_of_room(room, item_id, "item.remove")
    # Un item deja acte porte un resultat fige : le retirer reecrirait
    # l'historique, que le design interdit explicitement.
    if item.results.exists():
        raise RoomError("state.invalid_transition", "Item already decided", "item.remove")
    rnd = item.round
    item.delete()
    for index, remaining in enumerate(rnd.items.all(), start=1):
        if remaining.sequence != index:
            remaining.sequence = index
            remaining.save(update_fields=["sequence"])
    room.touch()
    return item_id


def reorder_items(room, participant, item_ids):
    _require_facilitator(room, participant, "item.reorder")
    rnd = _current_round(room)
    known = {i.id: i for i in (rnd.items.all() if rnd else [])}
    if set(item_ids) != set(known):
        raise RoomError("state.invalid_transition", "Item set mismatch", "item.reorder")
    for index, item_id in enumerate(item_ids, start=1):
        item = known[item_id]
        item.sequence = index
        item.save(update_fields=["sequence"])
    room.touch()
    return items_payload(rnd)


def build_agenda(room):
    """Le scenario : chaque ROUND de la salle avec son etat et, s'il a ete acte, la
    valeur retenue. L'`id` est desormais un id de round — le front le renvoie tel
    quel, donc l'alias `subject.select` reste juste sans rien savoir du changement.
    """
    current_id = room.current_round_id
    out = []
    for rnd in room.rounds.all().order_by("created_at", "id").prefetch_related("items", "result"):
        first = rnd.items.first()
        result = rnd.result.chosen_value if hasattr(rnd, "result") else None
        status = "current" if rnd.id == current_id else ("done" if result is not None else "pending")
        out.append({
            "id": rnd.id,
            "text": first.text if first else "",
            "status": status,
            "result": result,
            "items": items_payload(rnd),
        })
    return out


def select_round(room, participant, round_id):
    """Ex-`select_subject` : reprendre un round du scenario le remet a idle."""
    _require_facilitator(room, participant, "round.select")
    rnd = room.rounds.filter(id=round_id).first()
    if rnd is None:
        raise RoomError("state.invalid_transition", "Unknown round", "round.select")
    if rnd.state != RoundState.IDLE:
        rnd.state = RoundState.IDLE
        rnd.opened_at = None
        rnd.revealed_at = None
        rnd.vote_deadline = None
        rnd.facilitator = participant
        rnd.save(update_fields=["state", "opened_at", "revealed_at", "vote_deadline", "facilitator"])
        rnd.votes.all().delete()
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    room.touch()
    first = rnd.items.first()
    return {"roundId": rnd.id, "items": items_payload(rnd), "text": first.text if first else ""}


def add_scenario_item(room, participant, text):
    """Ex-`add_subject` : ajoute une entree au scenario, donc un ROUND de plus.
    Retourne l'id du round cree — c'est lui que l'agenda designe."""
    _require_facilitator(room, participant, "item.add")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.add")
    rnd = _new_round(room, participant, text)
    if room.current_round_id is None:
        room.current_round = rnd
        room.save(update_fields=["current_round"])
    room.touch()
    return rnd.id
```

Adapter ensuite les trois lecteurs restants :

- `prepare_round(...)` : `subject_id` désigne maintenant un round → `select_round(room, participant, subject_id)` ; `subject_text` → `set_current_item(...)` ; la garde `not rnd.subject.text.strip()` devient `not (_first_item(rnd) and _first_item(rnd).text.strip())` ; `summary["subject"]` = `current_item_text(room)`.
- `open_vote(...)` : même garde.
- `act_result(...)` : `defaults={"item": _first_item(rnd), "chosen_value": chosen_value, "decided_by": participant}` (la clé `subject` disparaît).
- `build_state_sync(...)` : `subject_text = current_item_text(room)` et deux clés de plus dans le payload :

```python
        # Les items du round courant. `subject` reste emis a cote, en doublon
        # deprecie, le temps que Facilitation_frontend bascule (design §5) : les
        # alias meurent en 5b, pas avant.
        "items": items_payload(rnd),
        "round": {"id": rnd.id if rnd else None, "state": round_state},
```

- [ ] **Step 4: Lancer les tests du domaine**

Run: `.\.venv\Scripts\python.exe -m pytest realtime/tests/test_items_services.py -q`
Expected: 6 PASS.

- [ ] **Step 5: Lancer la suite complète et réparer les appelants**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: les tests du consumer échouent encore (`services.set_subject` n'existe plus) — c'est la tâche 4. **Ne pas commiter un état rouge** : enchaîner la tâche 4 avant de commiter, ou garder pour ce commit des alias de compatibilité dans `services` :

```python
# Alias herites, le temps que le consumer bascule (tache 4). A supprimer ensuite.
set_subject = set_current_item
add_subject = add_scenario_item
select_subject = select_round
current_subject_text = current_item_text
```

- [ ] **Step 6: Commit**

```bash
git add realtime/services.py realtime/tests/test_items_services.py
git commit -m "Le domaine manipule les items du round"
```

---

### Task 4: Le contrat gagne `item.*` et `round.select`

**Files:**
- Modify: `realtime/consumers.py:72-84` (branches `subject.*`), `:86-108` (`round.prepare`), helpers `_broadcast_agenda` / `_broadcast_current_subject`
- Modify: `realtime/tests/test_consumer.py:119-120` (`_current_subject_id` → `_current_round_id`)
- Test: `realtime/tests/test_items_ws.py`

**Interfaces:**
- Consumes: les fonctions de la tâche 3.
- Produces (entrants) : `item.add {text}`, `item.update {itemId, text}`, `item.remove {itemId}`, `item.reorder {itemIds: []}`, `round.select {roundId}`.
- Produces (sortants) : `item.added`, `item.updated`, `item.removed`, `item.reordered`, tous de forme `{"roundId": int, "items": [...], "itemId": int|null}` ; `round.selected {"roundId", "items", "nextState": "idle"}`.
- Les types hérités `subject.set` / `subject.add` / `subject.select` restent acceptés et **continuent d'émettre** `subject.updated` et `agenda.updated`.

- [ ] **Step 1: Écrire les tests du contrat**

Créer `realtime/tests/test_items_ws.py` :

```python
"""Les nouveaux types item.* et la survie des alias herites (design §5).

Les alias existent pour que cette livraison soit PUREMENT back : le front ne
bascule qu'en 5b. Un test qui les couvre est donc un test de deploiement, pas une
politesse.
"""
import pytest
from channels.db import database_sync_to_async

from realtime.tests.test_consumer import _drain_until, _join, _make_room


@pytest.mark.django_db(transaction=True)
async def test_item_add_broadcasts_the_full_item_list():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Embauche ?"}})
    msg = await _drain_until(voter, "item.added", pred=lambda p: len(p["items"]) == 2)

    assert [i["text"] for i in msg["payload"]["items"]] == ["Budget ?", "Embauche ?"]
    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_a_voter_cannot_add_an_item():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    voter, _ = await _join(voter_token, code)

    await voter.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Mon post-it"}})
    msg = await _drain_until(voter, "error")

    assert msg["payload"]["rejectedType"] == "item.add"
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_legacy_subject_set_still_works():
    """Le front d'aujourd'hui n'envoie que `subject.set` : il doit continuer a
    jouer, sans modification, pendant toute la livraison 5a."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)

    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget ?"}})
    msg = await _drain_until(voter, "subject.updated")

    assert msg["payload"]["text"] == "Budget ?"
    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_round_select_replays_the_agenda_id():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "subject.add", "payload": {"text": "Q1"}})
    await fac.send_json_to({"v": 1, "type": "subject.add", "payload": {"text": "Q2"}})
    agenda_msg = await _drain_until(fac, "agenda.updated", pred=lambda p: len(p["agenda"]) == 2)
    second = agenda_msg["payload"]["agenda"][1]["id"]

    await fac.send_json_to({"v": 1, "type": "round.select", "payload": {"roundId": second}})
    msg = await _drain_until(fac, "round.selected")

    assert msg["payload"]["roundId"] == second
    assert [i["text"] for i in msg["payload"]["items"]] == ["Q2"]
    await fac.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_state_sync_carries_items_and_subject():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "item.add", "payload": {"text": "Budget ?"}})
    await _drain_until(fac, "item.added")
    fac2, sync = await _join(fac_token, code)

    assert sync["payload"]["subject"] == "Budget ?"
    assert [i["text"] for i in sync["payload"]["items"]] == ["Budget ?"]
    await fac.disconnect()
    await fac2.disconnect()
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `.\.venv\Scripts\python.exe -m pytest realtime/tests/test_items_ws.py -q`
Expected: FAIL — l'erreur reçue est `Unknown type item.add`.

- [ ] **Step 3: Brancher les nouveaux types dans `_dispatch`**

Dans `realtime/consumers.py`, remplacer les trois branches `subject.*` par :

```python
        # --- items du round (design 2026-09-11 §5) -----------------------
        if mtype == "item.add":
            item = await database_sync_to_async(services.add_item)(room, participant, payload.get("text", ""))
            await self._broadcast_items(room, "item.added", item["id"])
            await self._broadcast_agenda(room)
            await self._broadcast_current_item(room)
        elif mtype == "item.update":
            item = await database_sync_to_async(services.update_item)(
                room, participant, payload.get("itemId"), payload.get("text", "")
            )
            await self._broadcast_items(room, "item.updated", item["id"])
            await self._broadcast_agenda(room)
            await self._broadcast_current_item(room)
        elif mtype == "item.remove":
            item_id = await database_sync_to_async(services.remove_item)(room, participant, payload.get("itemId"))
            await self._broadcast_items(room, "item.removed", item_id)
            await self._broadcast_agenda(room)
        elif mtype == "item.reorder":
            await database_sync_to_async(services.reorder_items)(room, participant, payload.get("itemIds") or [])
            await self._broadcast_items(room, "item.reordered", None)
        elif mtype == "round.select":
            out = await database_sync_to_async(services.select_round)(
                room, participant, payload.get("roundId")
            )
            self._cancel_timeout(room.code)
            await self._broadcast("vote.wasReset", {"nextState": "idle"})
            await self._broadcast("round.selected", {**out, "nextState": "idle"})
            # Herite : le front d'aujourd'hui n'ecoute que ceux-la (alias, 5b).
            await self._broadcast("subject.updated", {"text": out["text"]})
            await self._broadcast_agenda(room)
        # --- alias herites, supprimes en 5b -----------------------------
        elif mtype == "subject.set":
            text = await database_sync_to_async(services.set_current_item)(room, participant, payload.get("text", ""))
            await self._broadcast("subject.updated", {"text": text})
            await self._broadcast_items(room, "item.updated", None)
            await self._broadcast_agenda(room)
        elif mtype == "subject.add":
            await database_sync_to_async(services.add_scenario_item)(room, participant, payload.get("text", ""))
            await self._broadcast_agenda(room)
            await self._broadcast_current_item(room)
        elif mtype == "subject.select":
            return await self._dispatch("round.select", {"roundId": payload.get("subjectId")}, cid)
```

Ajouter les deux helpers, à côté de `_broadcast_agenda` :

```python
    async def _broadcast_items(self, room, mtype, item_id):
        rnd = await database_sync_to_async(services.current_round)(room)
        items = await database_sync_to_async(services.items_payload)(rnd)
        await self._broadcast(mtype, {
            "roundId": rnd.id if rnd else None, "items": items, "itemId": item_id,
        })

    async def _broadcast_current_item(self, room):
        text = await database_sync_to_async(services.current_item_text)(room)
        await self._broadcast("subject.updated", {"text": text})
```

`_broadcast_current_subject` disparaît (remplacé par `_broadcast_current_item`). Exposer `services.current_round = _current_round` (alias public) ou renommer `_current_round` en `current_round` et corriger ses appelants.

Dans `round.prepare`, `payload.get("subjectId")` désigne désormais un round : passer `round_id=payload.get("subjectId")` à `prepare_round` et renommer le paramètre côté services (`subject_id` → `round_id`, `subject_text` → `item_text`), en gardant les clés du **payload WS** inchangées.

- [ ] **Step 4: Adapter le helper du test existant**

Dans `realtime/tests/test_consumer.py` :

```python
def _current_round_id(code):
    return Room.objects.get(code=code).current_round_id
```

et remplacer les deux usages de `_current_subject_id` par `_current_round_id` (les assertions comparent des identités de round : la sémantique est la même).

- [ ] **Step 5: Lancer la suite complète**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: **257 passed** (240 + 3 + 3 + 6 + 5, aucun test supprimé). Si un test du consumer échoue sur un message inattendu, vérifier l'ORDRE des diffusions : `_drain_until` tolère les intercalaires, mais pas une limite de 8 messages dépassée.

- [ ] **Step 6: Commit**

```bash
git add realtime/consumers.py realtime/tests/test_items_ws.py realtime/tests/test_consumer.py
git commit -m "Contrat : item.add/update/remove/reorder et round.select"
```

---

### Task 5: `history` lit l'item

**Files:**
- Modify: `history/api_views.py:41,49`
- Modify: `history/tests/test_history.py:34-36`

**Interfaces:**
- Consumes: `Result.item` (tâches 1-2).
- Produces: aucune API changée — la clé JSON reste `"subject"`, c'est le **read model** exposé au front, et le renommer casserait `history-detail.component.ts` sans rien apporter à cette livraison.

- [ ] **Step 1: Adapter le helper du test, vérifier qu'il échoue**

Dans `history/tests/test_history.py`, remplacer la fabrique :

```python
def _acted_result(room, text, value, seq=1):
    rnd = Round.objects.create(room=room, state=RoundState.ACTED)
    item = Item.objects.create(round=rnd, text=text, sequence=seq)
    return Result.objects.create(round=rnd, item=item, chosen_value=value)
```

Run: `.\.venv\Scripts\python.exe -m pytest history -q`
Expected: FAIL — `RelatedObjectDoesNotExist` ou `subject` null sur `_entries_for`.

- [ ] **Step 2: Repointer la vue**

Dans `history/api_views.py` :

```python
        .select_related("item", "round__room")
```
```python
                "subject": r.item.text,
```

- [ ] **Step 3: Lancer les tests**

Run: `.\.venv\Scripts\python.exe -m pytest history -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add history/api_views.py history/tests/test_history.py
git commit -m "L'historique lit l'item du resultat"
```

---

### Task 6: Supprimer `Subject`

**Files:**
- Modify: `rooms/models.py` (suppression de `Subject`, de `Round.subject`, de `Result.subject` ; `Result.item` passe non-null)
- Create: `rooms/migrations/0012_drop_subject.py`
- Modify: `realtime/services.py` (import, alias hérités supprimés)

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: plus aucune référence à `Subject` dans le dépôt hors `migrations/`.

- [ ] **Step 1: Vérifier que plus personne ne lit `Subject`**

Run: `git grep -n "Subject\|subject_id\|\.subject\b" -- "*.py" | grep -v migrations`
Expected: seules restent les clés JSON `"subject"` (contrat et history) et les paramètres de payload WS. Si un lecteur Python subsiste, le traiter avant d'aller plus loin.

- [ ] **Step 2: Supprimer le modèle et les champs**

Dans `rooms/models.py` : supprimer la classe `Subject` en entier, le champ `Round.subject`, le champ `Result.subject`, et passer `Result.item` en non-null :

```python
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="results")
```

Ajouter la contrainte du design §3 sur `Result` (un résultat par item d'un round) :

```python
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("round", "item"), name="uniq_result_round_item"),
        ]
```

et remplacer `round = models.OneToOneField(...)` par :

```python
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="results")
```

⚠️ `build_agenda` et `build_state_sync` utilisent `hasattr(rnd, "result")` / `rnd.result` : adapter en `rnd.results.first()`. `revealed_payload` et `act_result` idem.

- [ ] **Step 3: Générer et relire la migration**

Run: `.\.venv\Scripts\python.exe manage.py makemigrations rooms -n drop_subject`
Expected: `RemoveField` ×2, `AlterField` sur `Result.item` et `Result.round`, `AddConstraint`, `DeleteModel Subject`. **Relire** : si Django propose une valeur par défaut pour `Result.item`, c'est que la migration de données ne l'a pas rempli partout — corriger 0011 plutôt que d'accepter un défaut.

- [ ] **Step 4: Supprimer les alias de services**

Retirer le bloc `set_subject = set_current_item` … ajouté en tâche 3 step 5.

- [ ] **Step 5: Lancer la suite complète**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: 257 passed.

- [ ] **Step 6: Commit**

```bash
git add rooms/models.py rooms/migrations/0012_drop_subject.py realtime/services.py
git commit -m "Supprime Subject : le round porte ses items"
```

---

### Task 7: PostgreSQL, contrat, documentation

**Files:**
- Modify: `docs/superpowers/specs/delegation-poker-realtime-contract.md`
- Modify: `CLAUDE.md` (tableau du vocabulaire, plan)

**Interfaces:**
- Consumes: tout.
- Produces: la livraison est vérifiée sur le moteur de production et le contrat dit ce que le code fait.

- [ ] **Step 1: Rejouer la suite sur PostgreSQL**

```powershell
$env:DB_ENGINE="postgresql"; $env:DB_NAME="facilitation"; $env:DB_HOST="127.0.0.1"
$env:DB_PORT="5432"; $env:DB_USER="facilitation"; $env:DB_PASSWORD="<mdp local>"
.\.venv\Scripts\python.exe -m pytest -q
```
Expected: 257 passed. **Ce passage n'est pas optionnel** : les trois cas de migration et la contrainte `uniq_result_round_item` sont exactement ce que sqlite laisse filer.

- [ ] **Step 2: Étendre le contrat**

Dans `delegation-poker-realtime-contract.md`, ajouter une section « Items du round (5a) » : les cinq entrants (`item.add`, `item.update`, `item.remove`, `item.reorder`, `round.select`), leurs sortants, la forme `{roundId, items, itemId}`, et une note datée disant que `subject.set/add/select`, `subject.updated` et `agenda.updated` sont des **alias hérités supprimés en 5b**. Ajouter `items` et `round` à la description de `state.sync`.

- [ ] **Step 3: Mettre `CLAUDE.md` à jour**

- Tableau du vocabulaire : `Item` passe de « à renommer depuis `Subject` » à **existe** (migrations 0010-0012).
- Section « Plan, dans l'ordre » : rayer l'étape 5 au profit du programme 5a→5e et pointer les deux documents (`docs/superpowers/specs/2026-09-11-scenario-et-items-design.md`, ce plan).
- Écarts connus : retirer la mention de `origin_item` parmi les champs inexistants (il existe, inutilisé jusqu'en 5e).
- Référence de la suite : 240 → **251 passed**.

- [ ] **Step 4: Commit et PR**

```bash
git add docs CLAUDE.md
git commit -m "Contrat et doc : les items du round"
git push -u origin feat/5a-items-du-round
gh pr create --fill
```

---

## Vérification finale

- [ ] `pytest` vert sur sqlite **et** sur PostgreSQL.
- [ ] Une partie de Delegation Poker complète jouée à la main (créer une room, poser un sujet, ouvrir, voter à deux, révéler, acter, passer au sujet suivant) **avec le front actuel non modifié** — c'est le critère qui prouve que les alias tiennent.
- [ ] `git grep Subject -- "*.py" | grep -v migrations` ne renvoie rien.
- [ ] Les e2e front (`vote-cycle`, `round-flow`, `team-room`, `protocol`) passent contre le back de cette branche, sans modification du dépôt front.
