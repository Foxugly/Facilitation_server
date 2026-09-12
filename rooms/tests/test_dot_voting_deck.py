"""Design doc §7 : une activite sans cartes doit se ranger dans le snapshot sans
tordre l'architecture. Le deck dot_voting ne cree aucune carte active -- le snapshot
doit quand meme porter le bon voteType/resolutionStrategy, avec cards: []."""
import pytest
from django.core.management import call_command

from decks.models import Deck, VoteType
from decks.seed import create_dot_voting_deck
from rooms.snapshot import build_deck_snapshot


@pytest.mark.django_db
def test_snapshot_of_a_cardless_deck_carries_its_vote_type_and_no_cards():
    deck = create_dot_voting_deck()

    snapshot = build_deck_snapshot(deck)

    assert snapshot["voteType"] == "dot_voting"
    assert snapshot["resolutionStrategy"] == "dot_voting_v1"
    assert snapshot["cards"] == []


@pytest.mark.django_db
def test_command_is_idempotent():
    call_command("seed_dot_voting_deck")
    call_command("seed_dot_voting_deck")

    assert VoteType.objects.filter(code="dot_voting").count() == 1
    assert Deck.objects.filter(vote_type__code="dot_voting").count() == 1
