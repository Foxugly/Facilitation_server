"""Compte + equipe pretes a l'emploi pour la suite e2e (Playwright).

Les fonctions d'equipe — timer de round, revelation anonyme, choix du deck — sont
reservees aux equipes payantes. Les exercer en e2e suppose donc un compte confirme,
credite, proprietaire d'une equipe : un parcours d'inscription complet, avec
confirmation par e-mail et paiement, qu'aucun test de bout en bout ne peut jouer.

Cette commande fabrique cet etat directement. Elle est **idempotente** : la rejouer
remet le mot de passe et les droits a plat sans dupliquer quoi que ce soit.

REFUS EN PRODUCTION : elle cree un compte a `subscription_bypass=True`, c'est-a-dire
un acces payant offert. La laisser executable en prod reviendrait a livrer un moyen
de se creder soi-meme. Le garde-fou porte sur les MEMES valeurs que la garde Sentry
de `config/settings/base.py` (STATE / DJANGO_ENV), pour qu'un seul et meme signal
gouverne « suis-je en production ».
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from decks.models import Deck
from teams.models import BackgroundStyle, Team, TeamMembership, TeamRole

EMAIL = "e2e@example.com"
PASSWORD = "e2e-password-1234"
TEAM_NAME = "E2E Team"

# Une salle d'equipe est reservee a ses membres (rooms.api_views : « Sign in to
# join this team session »). Tester l'anonymat ou le timer suppose donc un SECOND
# compte, membre de l'equipe : un participant anonyme se verrait refuser l'entree.
MEMBER_EMAIL = "e2e-member@example.com"
MEMBER_PASSWORD = "e2e-password-1234"


class Command(BaseCommand):
    help = "Cree (ou remet a plat) le compte et l'equipe utilises par la suite e2e."

    def handle(self, *args, **options):
        state = str(getattr(settings, "STATE", "")).strip().upper()
        django_env = os.environ.get("DJANGO_ENV", "").strip().lower()
        if state in {"PROD", "PRODUCTION"} or django_env == "prod":
            raise CommandError(
                "Refus : cette commande cree un compte a acces payant offert "
                "(subscription_bypass) et n'a rien a faire en production."
            )

        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=EMAIL,
            defaults={"display_name": "E2E", "is_active": True},
        )
        user.set_password(PASSWORD)
        user.email_confirmed = True
        user.subscription_bypass = True
        user.is_active = True
        user.save()

        team, team_created = Team.objects.get_or_create(name=TEAM_NAME, owner=user)
        TeamMembership.objects.get_or_create(
            team=team, user=user, defaults={"role": TeamRole.OWNER}
        )

        # Le second compte n'a PAS besoin de subscription_bypass : c'est le
        # proprietaire de l'equipe qui porte le droit payant, pas chaque membre.
        member, member_created = User.objects.get_or_create(
            email=MEMBER_EMAIL, defaults={"display_name": "Alex", "is_active": True}
        )
        member.set_password(MEMBER_PASSWORD)
        member.email_confirmed = True
        member.is_active = True
        member.save()
        TeamMembership.objects.get_or_create(
            team=team, user=member, defaults={"role": TeamRole.MEMBER}
        )

        # Toute la carte des decks actifs est activee sur l'equipe : c'est ce qui
        # rend le CHANGEMENT de deck observable en salle. Une salle anonyme ne voit
        # que le sous-ensemble gratuit et n'a donc aucun choix a faire.
        decks = list(Deck.objects.filter(is_active=True))
        team.decks.set(decks)

        # Fond impose par l'equipe : c'est ce qui pose `.room--custom-bg` sur la
        # salle. Sans lui, le harnais de captures ne photographierait jamais ce
        # cas — et une regle de style qui ne s'applique qu'a lui passerait
        # inapercue, comme l'a montre l'etape 3c.
        team.background_style = BackgroundStyle.COLOR
        team.background_color = "#1e293b"
        team.save(update_fields=["background_style", "background_color"])

        self.stdout.write(
            self.style.SUCCESS(
                f"e2e pret : {EMAIL} (owner) + {MEMBER_EMAIL} (membre) / {PASSWORD} — "
                f"equipe {team.name!r} ({len(decks)} deck(s) actives)"
                f"{' [owner cree]' if created else ''}"
                f"{' [membre cree]' if member_created else ''}"
                f"{' [equipe creee]' if team_created else ''}"
            )
        )
