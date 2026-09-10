"""Teams & membership (Phase 2 P2.2). A Team is owned by a User; members join via
a TeamMembership carrying a role. Invitations are emailed, single-use, and require
the invitee to be signed in to accept (login-required link, scope §4.1)."""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class SurfaceStyle(models.TextChoices):
    """How a surface is skinned. Without this discriminator a team carrying both a
    colour and an image left it undefined which one applied — they were in fact
    both sent to the room."""

    COLOR = "color", "Flat colour"
    IMAGE = "image", "Image from the catalogue"


class BackgroundStyle(models.TextChoices):
    """How the room page behind the table is skinned.

    Deliberately NOT ``SurfaceStyle``: the background has a third, default state
    where the team imposes nothing and the page keeps the viewer's light/dark
    theme. Without it, every existing team would inherit a fixed colour and lose
    dark mode in the room.
    """

    THEME = "theme", "Follow the light/dark theme"
    COLOR = "color", "Flat colour"
    IMAGE = "image", "Image from the catalogue"


class ResultLayout(models.TextChoices):
    """Comment le depouillement occupe la place de la main, une fois les votes reveles.

    CARDS montre les cartes jouees : la seule forme lisible sur un deck a pictogrammes,
    dont les cartes n'ont aucun libelle et ou une ligne de texte n'aurait que la valeur
    brute a afficher. SUMMARY en fait des lignes chiffrees, plus grandes, ou la liste
    des votants tient sans etre tronquee.
    """

    CARDS = "cards", "Played cards"
    SUMMARY = "summary", "Tallied summary"


class TeamRole(models.TextChoices):
    """Team-scoped roles. Deliberately NOT "facilitator": that word is taken by
    ``rooms.Role.FACILITATOR``, who runs the current round — a per-round role any
    member can hold and hand over. A manager administers the team, and may never
    facilitate a single round."""

    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    MEMBER = "member", "Member"


def generate_invite_token() -> str:
    return secrets.token_urlsafe(32)


class Team(models.Model):
    name = models.CharField(max_length=120)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="owned_teams")
    created_at = models.DateTimeField(auto_now_add=True)
    # Appearance customization (P2.6): the team's room theme. Defaults mirror the
    # standard emerald felt + dark card-back base used by anonymous rooms.
    card_back_style = models.CharField(max_length=8, choices=SurfaceStyle.choices, default=SurfaceStyle.COLOR)
    card_back_color = models.CharField(max_length=9, default="#143d2f")
    felt_style = models.CharField(max_length=8, choices=SurfaceStyle.choices, default=SurfaceStyle.COLOR)
    felt_color = models.CharField(max_length=9, default="#10b981")
    # The page behind the table. THEME (default) paints nothing, so a team that
    # never touches this keeps the room exactly as it is today.
    background_style = models.CharField(max_length=8, choices=BackgroundStyle.choices, default=BackgroundStyle.THEME)
    background_color = models.CharField(max_length=9, default="#0f172a")
    result_layout = models.CharField(max_length=8, choices=ResultLayout.choices, default=ResultLayout.CARDS)
    # The poker types this team plays. A room freezes all of them and the
    # facilitator switches between them round by round. Empty = the standard deck.
    decks = models.ManyToManyField("decks.Deck", blank=True, related_name="teams_enabled")
    # The card back, picked independently of the fronts. Null = the deck's own default.
    card_back = models.ForeignKey("decks.CardBack", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    felt = models.ForeignKey("decks.Felt", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    background = models.ForeignKey(
        "decks.Background", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Billing (P2.7) is account-level: a team is "paid" via its owner's
    # billing.Subscription (plan quota), not a per-team subscription.

    def __str__(self):
        return self.name


class TeamMembership(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships")
    role = models.CharField(max_length=12, choices=TeamRole.choices, default=TeamRole.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("team", "user"), name="uniq_membership_team_user"),
        ]

    def __str__(self):
        return f"{self.user_id}@{self.team_id} ({self.role})"


class Invitation(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=12, choices=TeamRole.choices, default=TeamRole.MEMBER)
    token = models.CharField(max_length=64, unique=True, default=generate_invite_token)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.expires_at > timezone.now()

    def __str__(self):
        return f"invite {self.email} -> {self.team_id}"
