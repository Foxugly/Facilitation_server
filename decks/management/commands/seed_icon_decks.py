from django.core.management.base import BaseCommand

from decks.models import Deck
from decks.seed import create_fist_of_five_deck, create_roman_vote_deck


class Command(BaseCommand):
    help = "Create the two icon decks (Fist of Five, Roman Vote)."

    # Idempotence type par type : une execution partielle est relancable sans effet
    # de bord, chaque deck etant ignore separement s'il existe deja.
    DECKS = [
        ("fist_of_five", create_fist_of_five_deck),
        ("roman_vote", create_roman_vote_deck),
    ]

    def handle(self, *args, **options):
        for code, factory in self.DECKS:
            if Deck.objects.filter(vote_type__code=code, is_standard=True).exists():
                self.stdout.write(self.style.WARNING(f"Deck {code} already exists — skipping."))
                continue
            deck = factory()
            self.stdout.write(
                self.style.SUCCESS(f"Created {code} deck {deck.pk} with {deck.cards.count()} cards.")
            )
        self.stdout.write("Both decks are free_tier=False: enable them on a paid team to play.")
