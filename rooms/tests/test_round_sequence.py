"""`Round.sequence` : l'ordre du scenario devient explicite (tache 1, design
2026-09-12). Avant ce champ, l'agenda suivait `created_at` -- un facilitateur
ne pouvait pas composer sa file de rounds avant de la jouer.
"""
import pytest

from realtime import services
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Round, RoundState, Room
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


def _facilitator(room):
    return Participant.objects.create(
        room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR
    )


@pytest.mark.django_db
def test_three_rounds_take_sequences_1_2_3(standard_deck):
    """`_new_round` (passe par `add_scenario_item`) attribue la sequence
    SUIVANTE de la salle -- jamais celle de sa source."""
    room = _room(standard_deck)
    fac = _facilitator(room)

    services.add_scenario_item(room, fac, "Budget ?")
    services.add_scenario_item(room, fac, "Embauche ?")
    services.add_scenario_item(room, fac, "Conges ?")

    sequences = list(Round.objects.filter(room=room).order_by("id").values_list("sequence", flat=True))
    assert sequences == [1, 2, 3]


@pytest.mark.django_db
def test_agenda_orders_by_sequence_not_by_creation(standard_deck):
    """Sequences qui CONTREDISENT l'ordre de creation : si l'agenda triait
    encore sur `created_at`, ce test echouerait. Cree dans l'ordre A, B, C
    (donc created_at croissant dans cet ordre), mais numerote C=1, A=2, B=3."""
    room = _room(standard_deck)
    a = Round.objects.create(room=room, state=RoundState.IDLE, sequence=2)
    b = Round.objects.create(room=room, state=RoundState.IDLE, sequence=3)
    c = Round.objects.create(room=room, state=RoundState.IDLE, sequence=1)
    for rnd, text in ((a, "A"), (b, "B"), (c, "C")):
        rnd.items.create(text=text, sequence=1)

    agenda = services.build_agenda(room)

    assert [entry["text"] for entry in agenda] == ["C", "A", "B"]
    assert [entry["id"] for entry in agenda] == [c.id, a.id, b.id]


@pytest.mark.django_db(transaction=True)
def test_migration_backfills_sequence_by_creation_order_per_room():
    """0019 numerote les rounds existants selon `(created_at, id)`, et LE
    COMPTEUR EST PAR SALLE : les rounds de la seconde salle sont crees
    ENTRELACES avec ceux de la premiere (ids qui alternent) pour prouver qu'un
    compteur global (qui continuerait a 3, 4...) serait faux.
    """
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.utils import timezone

    def _migrate(targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    old = _migrate([("rooms", "0018_round_sequence")])

    VoteType = old.get_model("decks", "VoteType")
    Room = old.get_model("rooms", "Room")
    RoundHist = old.get_model("rooms", "Round")

    vt = VoteType.objects.create(code="seq-test", resolution_strategy="delegation_v1")

    def _mk_room(code):
        return Room.objects.create(
            code=code,
            vote_type=vt,
            deck_snapshot={"cards": []},
            expires_at=timezone.now() + timezone.timedelta(hours=8),
        )

    room1 = _mk_room("AAAA1111")
    room2 = _mk_room("BBBB2222")

    # Entrelace : r1a, r2a, r1b, r2b, r1c -- la seconde salle n'est pas
    # "apres" la premiere dans la suite des ids.
    r1a = RoundHist.objects.create(room=room1, state="idle")
    r2a = RoundHist.objects.create(room=room2, state="idle")
    r1b = RoundHist.objects.create(room=room1, state="idle")
    r2b = RoundHist.objects.create(room=room2, state="idle")
    r1c = RoundHist.objects.create(room=room1, state="idle")

    try:
        new = _migrate([("rooms", "0019_backfill_round_sequence")])
        RoundNew = new.get_model("rooms", "Round")

        def _seq(rnd_id):
            return RoundNew.objects.get(id=rnd_id).sequence

        assert [_seq(r1a.id), _seq(r1b.id), _seq(r1c.id)] == [1, 2, 3]
        # La seconde salle repart a 1 : un compteur global aurait donne 2, 4.
        assert [_seq(r2a.id), _seq(r2b.id)] == [1, 2]
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db
def test_removing_a_round_leaves_a_gap_but_next_round_does_not_collide(standard_deck):
    """Retirer un round du milieu laisse un trou dans les sequences (1, _, 3) --
    mais ce trou ne fait plus collisionner le round suivant (tache 2) :
    l'attribution est desormais fondee sur la sequence MAXIMALE existante
    (`services._next_round_sequence`), jamais sur `room.rounds.count() + 1`.

    Avant la tache 2, `count() + 1` retombait a 3 apres ce meme retrait (le
    compte redescend de 3 a 2) -- une valeur DEJA PORTEE par C, departagee
    uniquement par le tri secondaire sur `id`. Avec le maximum (toujours 3
    ici, meme apres le retrait de B), D prend 4 : aucune collision, donc plus
    besoin de departage pour cette file."""
    room = _room(standard_deck)
    fac = _facilitator(room)

    a_id = services.add_scenario_item(room, fac, "A")
    b_id = services.add_scenario_item(room, fac, "B")
    c_id = services.add_scenario_item(room, fac, "C")
    assert Round.objects.get(id=c_id).sequence == 3

    # Retrait direct (hors `services.remove_round`, qui renumerote) : le trou
    # doit survivre pour que ce test reste une preuve sur `_next_round_sequence`
    # seul, independante de la renumerotation que `remove_round` appliquerait.
    Round.objects.get(id=b_id).delete()

    d_id = services.add_scenario_item(room, fac, "D")
    # D ne retombe plus sur la sequence de C : le maximum (3) + 1 donne 4.
    assert Round.objects.get(id=d_id).sequence == 4
    assert Round.objects.get(id=c_id).sequence == 3

    agenda = services.build_agenda(room)
    assert [entry["id"] for entry in agenda] == [a_id, c_id, d_id]
