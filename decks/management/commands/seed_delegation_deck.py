from django.core.management.base import BaseCommand

from decks.models import Deck, VoteType
from decks.seed import create_standard_deck


class Command(BaseCommand):
    help = "Create the standard Delegation Poker deck (7 cards + translated layers)."

    def handle(self, *args, **options):
        if Deck.objects.filter(vote_type__code="delegation_poker", is_standard=True).exists():
            self.stdout.write(self.style.WARNING("Standard delegation_poker deck already exists — skipping."))
            return
        deck = create_standard_deck()
        self.stdout.write(self.style.SUCCESS(f"Created standard deck {deck.pk} with {deck.cards.count()} cards."))
        self.stdout.write("Card images are placeholders — upload the real illustrations in the admin.")
