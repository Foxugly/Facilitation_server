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
