"""Vote -> Response : rattachement a l'item du round + transposition en
payload (design 2026-09-11, tache 5b-2).

Meme structure que test_migration_items.py : MigrationExecutor plutot qu'un
appel direct a la fonction, pour verifier ce qui tournera vraiment en prod.
"""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("rooms", "0013_response")]
AFTER = [("rooms", "0014_votes_to_responses")]


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


def _room(apps, code):
    VoteType = apps.get_model("decks", "VoteType")
    Room = apps.get_model("rooms", "Room")
    vt = VoteType.objects.create(code=f"vt-{code}", resolution_strategy="delegation_v1")
    return Room.objects.create(
        code=code,
        vote_type=vt,
        deck_snapshot={"cards": []},
        expires_at=timezone.now() + timezone.timedelta(hours=8),
    )


def _seed(apps):
    """Une room, un round, un item, un participant, un vote "4"."""
    Round = apps.get_model("rooms", "Round")
    Item = apps.get_model("rooms", "Item")
    Participant = apps.get_model("rooms", "Participant")
    Response = apps.get_model("rooms", "Response")

    room = _room(apps, "AAAA1111")
    rnd = Round.objects.create(room=room, state="open")
    Item.objects.create(round=rnd, text="Budget ?", sequence=1)
    p = Participant.objects.create(room=room, token="tok-1", display_name="Alex")
    Response.objects.create(round=rnd, participant=p, card_value="4")


def _seed_round_without_item(apps):
    """Un round sans item (impossible aujourd'hui depuis 0011) et sa reponse
    orpheline, pour verifier que la migration ne plante pas dessus."""
    Round = apps.get_model("rooms", "Round")
    Participant = apps.get_model("rooms", "Participant")
    Response = apps.get_model("rooms", "Response")

    room = _room(apps, "BBBB2222")
    rnd = Round.objects.create(room=room, state="open")
    p = Participant.objects.create(room=room, token="tok-2", display_name="Sam")
    Response.objects.create(round=rnd, participant=p, card_value="7")


def test_vote_becomes_a_response_to_the_round_item(head):
    """Un vote existant vise desormais l'item de son round, et sa valeur de
    carte devient un payload - la forme que toutes les activites partageront."""
    old = _migrate(BEFORE)
    _seed(old)

    new = _migrate(AFTER)
    Response = new.get_model("rooms", "Response")

    r = Response.objects.get()
    assert r.payload == {"card": "4"}
    assert r.item.text == "Budget ?"
    assert r.card_value == "4"   # conserve : le front ne bascule qu'en tache 5


def test_a_round_without_item_leaves_its_responses_untouched(head):
    """Cas impossible aujourd'hui (tout round porte un item depuis 0011), mais
    la migration ne doit pas planter dessus : elle laisse la reponse en l'etat
    plutot que d'inventer un item que personne n'a pose."""
    old = _migrate(BEFORE)
    _seed_round_without_item(old)

    new = _migrate(AFTER)
    Response = new.get_model("rooms", "Response")

    r = Response.objects.get()
    assert r.item_id is None
    assert r.payload == {}
    assert r.card_value == "7"
