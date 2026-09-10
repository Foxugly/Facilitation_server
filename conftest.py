import pytest

from decks.seed import create_standard_deck


@pytest.fixture
def standard_deck(db):
    """The single standard Delegation Poker deck (7 cards, 2 layers each)."""
    return create_standard_deck()
