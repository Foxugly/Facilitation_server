"""L'etat envoye a la connexion doit decrire un round revele aussi fidelement que
l'evenement de revelation.

Sans cela, recharger la page pendant un round revele laissait les cartes du tapis face
cachee alors que le decompte s'affichait : `vote.revealed` transporte le lien
participant -> carte, `state.sync` ne le transportait pas. Deux chemins, deux verites.

L'invariant d'anonymat ne bouge pas pour autant : un round anonyme ne doit emettre aucun
lien participant -> carte, par quelque chemin que ce soit.
"""
import pytest
from django.contrib.auth import get_user_model

from decks.seed import create_standard_deck
from realtime import services
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room, Subject, VoteSession
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


def _room(team=None):
    deck = create_standard_deck()
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), team=team)
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    subject = Subject.objects.create(room=room, text="Deploys")
    session = VoteSession.objects.create(room=room, subject=subject, facilitator=fac)
    room.current_session = session
    room.save(update_fields=["current_session"])
    return room, fac, voter, session


@pytest.fixture
def paid_team(db):
    owner = User.objects.create_user(email="o@example.com", password="pw12345678", display_name="O")
    team = Team.objects.create(name="Acme", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    return team


@pytest.mark.django_db
def test_state_sync_of_a_revealed_round_carries_votes_and_spread(db):
    room, fac, voter, _ = _room()
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")
    services.cast_vote(room, fac, "6")
    services.reveal(room, fac)

    payload = services.build_state_sync(voter)

    assert payload["roundState"] == "revealed"
    assert payload["tally"] == [{"cardValue": "4", "count": 1}, {"cardValue": "6", "count": 1}]
    # Le lien participant -> carte : c'est lui qui fait retourner les cartes du tapis.
    assert {v["participantId"] for v in payload["votes"]} == {str(voter.public_id), str(fac.public_id)}
    # L'ecart aussi, sinon il disparait de l'ecran au rechargement.
    assert payload["spread"] == {"min": 4, "max": 6}


@pytest.mark.django_db
def test_state_sync_of_an_anonymous_revealed_round_emits_no_participant_card_link(paid_team):
    """L'invariant d'anonymat prime : le decompte, jamais le lien."""
    room, fac, voter, _ = _room(team=paid_team)
    services.set_reveal_mode(room, fac, True)
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")
    services.reveal(room, fac)

    payload = services.build_state_sync(voter)

    assert payload["tally"] == [{"cardValue": "4", "count": 1}]
    assert "votes" not in payload
    # Ceinture et bretelles : le decompte ne porte aucun identifiant. L'invariant est
    # l'absence de lien participant -> carte, non l'absence des participants : la liste
    # des presents figure legitimement dans l'etat, on voit qui est dans la salle.
    assert all(set(entree) == {"cardValue", "count"} for entree in payload["tally"])


@pytest.mark.django_db
def test_state_sync_of_an_acted_round_still_carries_the_detail(db):
    """Le client traite « acte » comme un round revele : meme besoin de detail."""
    room, fac, voter, _ = _room()
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")
    services.reveal(room, fac)
    services.act_result(room, fac, "4")

    payload = services.build_state_sync(voter)

    assert payload["roundState"] == "acted"
    assert payload["result"] == "4"
    assert payload["tally"] == [{"cardValue": "4", "count": 1}]
    assert [v["cardValue"] for v in payload["votes"]] == ["4"]


@pytest.mark.django_db
def test_state_sync_of_an_open_round_leaks_nothing(db):
    """Avant la revelation, personne ne doit connaitre la carte d'un autre."""
    room, fac, voter, _ = _room()
    services.open_vote(room, fac)
    services.cast_vote(room, voter, "4")

    payload = services.build_state_sync(fac)

    assert payload["roundState"] == "open"
    assert "votes" not in payload
    assert "tally" not in payload
    assert "spread" not in payload
