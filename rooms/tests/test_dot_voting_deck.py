"""Design doc §7 : une activite sans cartes doit se ranger dans le snapshot sans
tordre l'architecture. Le deck dot_voting ne cree aucune carte active -- le snapshot
doit quand meme porter le bon voteType/resolutionStrategy, avec cards: [].

Le deck est seme inactif : tant qu'aucune entree de registre ne connait
"dot_voting" (tache suivante), le proposer au catalogue reviendrait a promettre
un geste que le serveur refuserait ensuite -- regle tenue partout ailleurs dans
ce depot. Ces tests epinglent que ce drapeau tient vraiment le catalogue, et
qu'un rejeu du seed ne revient jamais sur une reactivation deliberee.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from decks.models import Deck, VoteType
from decks.seed import create_dot_voting_deck
from decks.selection import available_decks
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.mark.django_db
def test_snapshot_of_a_cardless_deck_carries_its_vote_type_and_no_cards():
    deck = create_dot_voting_deck()

    snapshot = build_deck_snapshot(deck)

    assert snapshot["voteType"] == "dot_voting"
    assert snapshot["resolutionStrategy"] == "dot_voting_v1"
    assert snapshot["cards"] == []


@pytest.mark.django_db
def test_seeded_deck_is_inactive_by_default():
    deck = create_dot_voting_deck()

    assert deck.is_active is False


@pytest.mark.django_db
def test_inactive_deck_is_excluded_from_a_team_catalog():
    """decks.selection.available_decks est la source unique du catalogue -- si ce
    test passe alors que le deck resterait injouable, c'est bien ce filtre qui
    tient le catalogue, pas une supposition."""
    create_dot_voting_deck()
    owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
    team = Team.objects.create(name="Squad", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)

    codes = {d.vote_type.code for d in available_decks(team)}

    assert "dot_voting" not in codes


@pytest.mark.django_db
def test_command_is_idempotent():
    call_command("seed_dot_voting_deck")
    call_command("seed_dot_voting_deck")

    assert VoteType.objects.filter(code="dot_voting").count() == 1
    assert Deck.objects.filter(vote_type__code="dot_voting").count() == 1


@pytest.mark.django_db
def test_replaying_the_command_never_reverts_a_deliberate_reactivation():
    """Un operateur qui rallume le deck (is_active=True) une fois l'activite
    jouable ne doit pas le voir redevenir inactif au prochain deploiement -- le
    seed skippe des qu'une ligne existe, il ne la met jamais a jour."""
    call_command("seed_dot_voting_deck")
    deck = Deck.objects.get(vote_type__code="dot_voting")
    deck.is_active = True
    deck.save()

    call_command("seed_dot_voting_deck")

    deck.refresh_from_db()
    assert deck.is_active is True
    assert Deck.objects.filter(vote_type__code="dot_voting").count() == 1
