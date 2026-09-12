"""Chaque round fige son propre deck des la preparation, pas seulement a
l'ouverture (task 1, design 5c). Sans cela, preparer un second round avec un
autre deck reecrirait le type du premier sous les pieds du facilitateur : un
scenario ne pourrait jamais enchainer deux activites differentes.
"""
import pytest

from decks.models import Deck
from decks.seed import create_standard_deck
from realtime import activities, services
from realtime.activities import ActivitySpec
from realtime.services import RoomError
from realtime.tests.helpers import cast_first_item
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, Round
from rooms.snapshot import build_deck_snapshot

pytestmark = pytest.mark.django_db


def _second_deck(vote_type):
    """Un second deck jouable, une seule carte suffit a distinguer les
    snapshots — et sa valeur n'existe pas dans le deck standard ("1".."7")."""
    deck = Deck.objects.create(vote_type=vote_type, is_standard=False, card_back_image="decks/backs/b.webp")
    deck.set_current_language("en")
    deck.name = "Fibonacci"
    deck.save()
    deck.cards.create(value="13", slug="thirteen", order=1, background_image="decks/cards/13.webp")
    return deck


@pytest.fixture
def room_with_two_decks():
    standard = create_standard_deck()
    other = _second_deck(standard.vote_type)
    snapshots = [build_deck_snapshot(standard), build_deck_snapshot(other)]
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(
        code=code, vote_type=standard.vote_type, deck_snapshot=snapshots[0],
        deck_snapshots=snapshots, title="Retro",
    )
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    return room, fac, standard, other


def test_second_round_with_another_deck_does_not_change_the_first(room_with_two_decks):
    """Deux rounds prepares dans la meme room avec deux decks differents gardent
    chacun le sien : preparer le second ne touche pas au deck_snapshot du
    premier, deja fige."""
    room, fac, standard, other = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    first_id = services.current_round(room).id

    second_id = services.add_scenario_item(room, fac, "B")
    services.prepare_round(room, fac, subject_id=second_id, deck_id=other.pk)

    first = Round.objects.get(id=first_id)
    second = Round.objects.get(id=second_id)
    assert first.deck_snapshot["deckId"] == standard.pk
    assert second.deck_snapshot["deckId"] == other.pk


def test_opening_a_round_does_not_overwrite_its_frozen_deck(room_with_two_decks):
    """Ouvrir un round n'ecrase pas le snapshot fige a sa preparation, meme si le
    deck ACTIF de la room a change entre-temps."""
    room, fac, standard, other = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    rnd_id = services.current_round(room).id

    # Le facilitateur change le deck actif de la room avant d'ouvrir : le round,
    # lui, a deja fige le sien a la preparation.
    services.select_deck(room, fac, other.pk)

    services.open_vote(room, fac)

    rnd = Round.objects.get(id=rnd_id)
    assert rnd.deck_snapshot["deckId"] == standard.pk


def test_round_prepared_without_explicit_deck_inherits_the_room_deck(room_with_two_decks):
    """Sans choix explicite de deck a la preparation, le round n'a pas encore de
    snapshot propre ; l'ouverture le fige alors sur celui de la room — le
    comportement d'aujourd'hui, celui d'une room a un seul deck (poker)."""
    room, fac, standard, _ = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A")
    rnd_id = services.current_round(room).id
    rnd = Round.objects.get(id=rnd_id)
    assert rnd.deck_snapshot is None

    services.open_vote(room, fac)

    rnd.refresh_from_db()
    assert rnd.deck_snapshot["deckId"] == standard.pk


def test_reset_then_reopen_keeps_the_rounds_own_deck(room_with_two_decks):
    """Reinitialiser un round (`vote.reset`) ne lui fait pas perdre son deck : le
    rouvrir doit retrouver le sien, pas le deck ACTIF courant de la room — qu'un
    AUTRE round prepare entre-temps a pu changer."""
    room, fac, standard, other = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    a_id = services.current_round(room).id

    b_id = services.add_scenario_item(room, fac, "B")
    services.prepare_round(room, fac, subject_id=b_id, deck_id=other.pk)

    # Revient sur A puis le reinitialise, avant de le rouvrir.
    services.select_round(room, fac, a_id)
    services.reset_round(room, fac)

    services.open_vote(room, fac)

    rnd = Round.objects.get(id=a_id)
    assert rnd.deck_snapshot["deckId"] == standard.pk


def test_replaying_an_acted_round_keeps_its_own_deck_and_config(room_with_two_decks):
    """Rejouer un round ACTE (via `select_round`) doit garder SON deck et SA
    config, pas ceux devenus actifs sur la room entretemps : rejouer, c'est la
    MEME activite avec des reponses neuves, pas une nouvelle activite."""
    room, fac, standard, other = room_with_two_decks
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)

    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    a_id = services.current_round(room).id
    rnd = Round.objects.get(id=a_id)
    rnd.config = {"anonymity": "off"}
    rnd.save(update_fields=["config"])

    services.open_vote(room, fac)
    cast_first_item(room, voter, "4")
    services.reveal(room, fac)
    services.act_result(room, fac, "4")

    # Le deck ACTIF de la room change APRES que A a ete acte.
    services.select_deck(room, fac, other.pk)

    out = services.select_round(room, fac, a_id)
    replay_id = out["roundId"]
    assert replay_id != a_id

    replay = Round.objects.get(id=replay_id)
    assert replay.deck_snapshot["deckId"] == standard.pk
    assert replay.config == {"anonymity": "off"}


def test_round_config_defaults_to_empty_dict_and_round_trips(room_with_two_decks):
    """`Round.config` vaut {} par defaut et survit a un aller-retour en base."""
    room, fac, _, _ = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A")
    rnd_id = services.current_round(room).id

    rnd = Round.objects.get(id=rnd_id)
    assert rnd.config == {}

    rnd.config = {"anonymity": "off"}
    rnd.save(update_fields=["config"])

    reloaded = Round.objects.get(id=rnd_id)
    assert reloaded.config == {"anonymity": "off"}


@pytest.fixture
def delegation_v1_with_note_option(monkeypatch):
    """Le poker (`delegation_v1`) n'a par defaut aucune option propre : pour
    verifier la validation d'une cle CONNUE (bon et mauvais type), on lui
    prete temporairement un `config_schema` non vide. Restaure automatiquement
    par `monkeypatch` en fin de test."""
    monkeypatch.setitem(
        activities.ACTIVITY_REGISTRY,
        "delegation_v1",
        ActivitySpec(ordinal=True, config_schema={"note": str}),
    )


def test_configure_round_accepts_and_persists_a_conforming_config(
    room_with_two_decks, delegation_v1_with_note_option
):
    room, fac, standard, _ = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    rnd_id = services.current_round(room).id

    out = services.configure_round(room, fac, rnd_id, config={"note": "post-mortem"})

    assert out["roundId"] == rnd_id
    assert out["config"] == {"note": "post-mortem"}
    assert Round.objects.get(id=rnd_id).config == {"note": "post-mortem"}


def test_configure_round_refuses_an_unknown_config_key(room_with_two_decks):
    """Le poker n'a aucune option propre aujourd'hui : son `config_schema` est
    vide, donc toute cle est une erreur cote client, pas une donnee a
    stocker."""
    room, fac, standard, _ = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    rnd_id = services.current_round(room).id

    with pytest.raises(RoomError) as exc:
        services.configure_round(room, fac, rnd_id, config={"timer": 30})
    assert exc.value.rejected_type == "round.configure"
    assert Round.objects.get(id=rnd_id).config == {}


def test_configure_round_refuses_a_wrong_value_type(
    room_with_two_decks, delegation_v1_with_note_option
):
    room, fac, standard, _ = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    rnd_id = services.current_round(room).id

    with pytest.raises(RoomError) as exc:
        services.configure_round(room, fac, rnd_id, config={"note": 42})
    assert exc.value.rejected_type == "round.configure"
    assert Round.objects.get(id=rnd_id).config == {}


def test_configure_round_rejects_atomically_leaving_deck_unchanged(room_with_two_decks):
    """Un appel qui change de deck ET porte une config refusee ne doit RIEN
    laisser ecrit, deck compris : avant le correctif, `select_deck` puis le
    report sur le round etaient deja en base au moment ou `validate_config`
    levait — un coup refuse etait quand meme applique pour moitie.

    Verifie par mutation : retirer `@transaction.atomic` sur `configure_round`
    fait echouer ce test (le deck du round ET celui de la room passent a
    `other` malgre le refus). Voir le rapport de tache pour la trace."""
    room, fac, standard, other = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    rnd_id = services.current_round(room).id

    with pytest.raises(RoomError) as exc:
        services.configure_round(room, fac, rnd_id, deck_id=other.pk, config={"timer": 30})
    assert exc.value.rejected_type == "round.configure"

    rnd = Round.objects.get(id=rnd_id)
    assert rnd.deck_snapshot["deckId"] == standard.pk
    room.refresh_from_db(fields=["deck_snapshot"])
    assert room.deck_snapshot["deckId"] == standard.pk


def test_configure_round_refuses_a_round_already_open(room_with_two_decks):
    """La configuration se fige avant l'ouverture, comme le mode de
    revelation : les participants doivent savoir a quoi ils jouent avant de
    voter."""
    room, fac, standard, _ = room_with_two_decks
    services.prepare_round(room, fac, subject_text="A", deck_id=standard.pk)
    rnd_id = services.current_round(room).id
    services.open_vote(room, fac)

    with pytest.raises(RoomError) as exc:
        services.configure_round(room, fac, rnd_id, config={})
    assert exc.value.rejected_type == "round.configure"
