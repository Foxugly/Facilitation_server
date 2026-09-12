"""Le domaine manipule des items de round (design 2026-09-11 §3, §5).

Tests synchrones : `services` est volontairement pauvre en framework, donc
testable sans socket.
"""
import pytest

from realtime import activities, services
from realtime.activities import ActivitySpec
from realtime.services import RoomError
from realtime.tests.helpers import cast_first_item
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Item, Participant, Role, Room, RoundState
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
    porte aux participants en 5c (`items_authored_by`).

    Verifie par mutation (correction ronde 1, trace dans le rapport de tache) :
    remplacer dans `add_item` la condition
    `spec_for(strategy).items_authored_by != "participants" and not is_facilitator`
    par `False` (le filtre du registre neutralise, donc plus aucune garde sur
    QUI peut poser un item) fait PASSER silencieusement l'appel ci-dessous — ce
    test echoue alors sur `pytest.raises` (`DID NOT RAISE`). Remettre la
    condition le fait a nouveau passer."""
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
def test_reorder_items_rejects_duplicate_ids(room_with_facilitator):
    """Round a UN SEUL item : set([a, a]) == {a} == known, donc seule la
    comparaison de longueur peut rejeter ce doublon. Avec un second item present
    (b non repris dans item_ids), le controle d'ensemble suffirait deja a lui
    seul et le test ne demontrerait rien sur la garde de longueur."""
    room, fac, _ = room_with_facilitator
    services.set_current_item(room, fac, "A")
    room.refresh_from_db()
    a = services.items_payload(room.current_round)[0]

    with pytest.raises(RoomError) as exc:
        services.reorder_items(room, fac, [a["id"], a["id"]])

    assert exc.value.rejected_type == "item.reorder"


@pytest.mark.django_db
def test_agenda_lists_rounds_and_select_round_resets_to_idle(room_with_facilitator):
    """L'agenda designe desormais des ROUNDS ; `select_round` (ex-`select_subject`)
    en reprend un et le remet a idle."""
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


@pytest.mark.django_db
def test_cannot_remove_an_item_from_a_round_in_flight(room_with_facilitator):
    """C1 : retirer le dernier item d'un round revele laissait `act_result` sans
    item ou accrocher son `Result` (colonne NOT NULL) — l'IntegrityError qui
    s'ensuivait n'est pas un RoomError et fermait la socket du facilitateur."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    item_id = services.items_payload(services.current_round(room))[0]["id"]
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)

    with pytest.raises(RoomError) as exc:
        services.remove_item(room, fac, item_id)

    assert exc.value.rejected_type == "item.remove"
    # Le round est reste actable : la garde protege, elle ne bloque pas.
    assert services.act_result(room, fac, "4") == "4"


@pytest.mark.django_db
def test_act_result_refuses_a_round_left_without_item(room_with_facilitator):
    """Defense en profondeur derriere la garde ci-dessus : si un item disparaissait
    par un autre chemin (cascade, script d'admin), `result.act` doit rendre un
    RoomError — le consumer ne rattrape que celui-la."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)
    Item.objects.filter(round=services.current_round(room)).delete()

    with pytest.raises(RoomError) as exc:
        services.act_result(room, fac, "4")

    assert exc.value.rejected_type == "result.act"


@pytest.mark.django_db
def test_cannot_rewrite_an_item_already_decided(room_with_facilitator):
    """I2 : `history/api_views.py` affiche `Result.item.text`. Reformuler un item
    deja acte changerait retroactivement le rapport envoye aux managers — meme
    garde que `remove_item`, qui refusait deja."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    item_id = services.items_payload(services.current_round(room))[0]["id"]
    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)
    services.act_result(room, fac, "4")

    with pytest.raises(RoomError) as exc:
        services.update_item(room, fac, item_id, "Budget 2027 ?")

    assert exc.value.rejected_type == "item.update"
    assert Item.objects.get(id=item_id).text == "Budget ?"


@pytest.fixture
def delegation_v1_authored_by_participants(monkeypatch):
    """Le poker (`delegation_v1`) ne declare pas `items_authored_by` — aucune
    activite du registre ne le fait aujourd'hui. Meme motif que
    `delegation_v1_with_note_option` (`test_round_type.py`) : preter
    temporairement au poker une politique qu'il n'a pas, pour tester le
    registre sans attendre qu'une vraie activite "participants" existe.
    Restaure automatiquement par `monkeypatch` en fin de test."""
    monkeypatch.setitem(
        activities.ACTIVITY_REGISTRY,
        "delegation_v1",
        ActivitySpec(ordinal=True, items_authored_by="participants"),
    )


@pytest.mark.django_db
def test_a_voter_may_add_an_item_when_the_activity_authors_by_participants(
    room_with_facilitator, delegation_v1_authored_by_participants
):
    """Sous `items_authored_by = "participants"`, le votant peut ecrire son
    propre post-it — et l'item porte son auteur en base (design §3 : `author`
    n'est jamais vide en base, meme si l'affichage le masque ensuite)."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")

    out = services.add_item(room, voter, "Mon post-it")

    item = Item.objects.get(id=out["id"])
    assert item.text == "Mon post-it"
    assert item.author_id == voter.id


@pytest.mark.django_db
def test_a_voter_may_edit_and_remove_their_own_item(
    room_with_facilitator, delegation_v1_authored_by_participants
):
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    mine = services.add_item(room, voter, "Mon post-it")

    services.update_item(room, voter, mine["id"], "Mon post-it corrige")
    assert Item.objects.get(id=mine["id"]).text == "Mon post-it corrige"

    services.remove_item(room, voter, mine["id"])
    assert not Item.objects.filter(id=mine["id"]).exists()


@pytest.mark.django_db
def test_a_voter_may_not_edit_or_remove_anothers_item(
    room_with_facilitator, delegation_v1_authored_by_participants
):
    """Le serveur fait autorite : un participant qui tente de toucher l'item
    d'un AUTRE participant recoit une `RoomError` dont `rejected_type`
    correspond a l'intention qu'il a emise, pas d'ecriture silencieusement
    ignoree.

    Verifie par mutation : neutraliser la garde de propriete dans
    `_require_item_author` (faire retourner la fonction sans lever, pour un
    participant non facilitateur) fait PASSER silencieusement cet `update_item`
    et laisse le texte change — ce test echoue alors sur l'assertion de texte.
    Restaurer la garde le fait a nouveau passer. Trace dans le rapport de
    tache."""
    room, fac, voter = room_with_facilitator
    other = Participant.objects.create(
        room=room, token=generate_token(), display_name="Bo", role=Role.VOTER
    )
    services.set_current_item(room, fac, "Budget ?")
    theirs = services.add_item(room, voter, "Post-it de Alex")

    with pytest.raises(RoomError) as exc:
        services.update_item(room, other, theirs["id"], "Je modifie Alex")
    assert exc.value.rejected_type == "item.update"
    # Code distinct de "forbidden.not_facilitator" (correction ronde 1) : `other`
    # n'a jamais pretendu faciliter, son refus porte sur la propriete de l'item.
    assert exc.value.code == "forbidden.not_item_author"
    assert Item.objects.get(id=theirs["id"]).text == "Post-it de Alex"

    with pytest.raises(RoomError) as exc:
        services.remove_item(room, other, theirs["id"])
    assert exc.value.rejected_type == "item.remove"
    assert exc.value.code == "forbidden.not_item_author"
    assert Item.objects.filter(id=theirs["id"]).exists()


@pytest.mark.django_db
def test_the_facilitator_may_edit_and_remove_any_item_under_participants_policy(
    room_with_facilitator, delegation_v1_authored_by_participants
):
    """Le facilitateur peut toujours editer et retirer n'importe quel item,
    meme ecrit par un participant (design §5) — sa main ne depend jamais de
    `Item.author`."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    theirs = services.add_item(room, voter, "Post-it de Alex")

    services.update_item(room, fac, theirs["id"], "Corrige par le facilitateur")
    assert Item.objects.get(id=theirs["id"]).text == "Corrige par le facilitateur"

    services.remove_item(room, fac, theirs["id"])
    assert not Item.objects.filter(id=theirs["id"]).exists()


@pytest.mark.django_db
def test_an_item_without_an_author_is_not_editable_by_an_ordinary_participant(
    room_with_facilitator, delegation_v1_authored_by_participants
):
    """`Item.author` est SET_NULL : un item dont l'auteur a quitte la salle a un
    auteur nul. Decision prise dans ce rapport : un item sans auteur ne devient
    PAS modifiable par n'importe quel participant — seul le facilitateur le
    reste. Sans cette regle, quitter la salle transformerait un post-it prive
    en post-it libre, l'inverse de ce que l'auteur attendait."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    orphan = services.add_item(room, voter, "Post-it orphelin")
    voter.delete()  # SET_NULL : Item.author_id devient None.

    other = Participant.objects.create(
        room=room, token=generate_token(), display_name="Bo", role=Role.VOTER
    )

    with pytest.raises(RoomError) as exc:
        services.update_item(room, other, orphan["id"], "Je recupere l'orphelin")
    assert exc.value.rejected_type == "item.update"
    assert exc.value.code == "forbidden.not_item_author"

    services.update_item(room, fac, orphan["id"], "Le facilitateur, lui, peut")
    assert Item.objects.get(id=orphan["id"]).text == "Le facilitateur, lui, peut"


@pytest.mark.django_db
def test_a_voter_cannot_open_a_round_by_adding_the_first_item(
    room_with_facilitator, delegation_v1_authored_by_participants
):
    """Meme sous `items_authored_by = "participants"`, un votant ne peut pas
    creer le PREMIER round d'une room en y ecrivant un post-it : `_new_round`
    assignerait la facilitation du round neuf a son appelant, ce qui ferait
    d'un simple participant le facilitateur du round qu'il vient de creer.
    Le scenario (ouvrir un round) reste un geste du facilitateur ; les
    participants n'ecrivent que dans un round deja courant."""
    room, fac, voter = room_with_facilitator
    assert services.current_round(room) is None

    with pytest.raises(RoomError) as exc:
        services.add_item(room, voter, "Mon post-it")

    assert exc.value.rejected_type == "item.add"
    assert services.current_round(room) is None


@pytest.mark.django_db
def test_a_voter_cannot_reorder_items_even_when_they_may_add_their_own(
    room_with_facilitator, delegation_v1_authored_by_participants
):
    """Arbitrage ronde 1 (`services.reorder_items`) : creer et editer SA PROPRE
    contribution ne donne pas le droit de reordonner LA LISTE ENTIERE du round.
    Reordonner est un geste de facilitation (ranger un tableau), pas un droit
    d'auteur — un participant qui deplacerait les post-its des autres pour
    faire remonter le sien detournerait l'activite. `reorder_items` reste donc
    facilitateur seul meme quand ce meme votant peut, dans le meme round,
    ajouter et editer son propre item."""
    room, fac, voter = room_with_facilitator
    services.set_current_item(room, fac, "Budget ?")
    mine = services.add_item(room, voter, "Mon post-it")
    room.refresh_from_db()
    rnd = services.current_round(room)
    before = [i["id"] for i in services.items_payload(rnd)]

    # Le meme votant peut creer ET editer son propre item (la politique ouvre
    # bien l'ecriture) : ce n'est pas un probleme d'autorite generale.
    services.update_item(room, voter, mine["id"], "Mon post-it corrige")

    with pytest.raises(RoomError) as exc:
        services.reorder_items(room, voter, list(reversed(before)))

    assert exc.value.rejected_type == "item.reorder"
    assert exc.value.code == "forbidden.not_facilitator"
    assert [i["id"] for i in services.items_payload(rnd)] == before
