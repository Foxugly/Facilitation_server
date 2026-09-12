from django.core.management.base import BaseCommand

from decks.models import Deck
from decks.seed import DOT_VOTING_CODE, create_dot_voting_deck


class Command(BaseCommand):
    help = "Create the Dot Voting vote type and its deck (no cards)."

    def handle(self, *args, **options):
        if Deck.objects.filter(vote_type__code=DOT_VOTING_CODE, is_standard=True).exists():
            self.stdout.write(self.style.WARNING("Standard dot_voting deck already exists -- skipping."))
            return
        deck = create_dot_voting_deck()
        self.stdout.write(self.style.SUCCESS(f"Created dot_voting deck {deck.pk} with {deck.cards.count()} cards."))
        self.stdout.write("This deck has no active cards by design -- see design doc §7.")
