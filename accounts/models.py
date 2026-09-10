import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """Email-based manager (fleet convention, OPERATIONS.md §3.16).

    The stock ``UserManager`` keys off ``username``, which this model drops, so
    ``createsuperuser`` and programmatic creation must go through email instead.
    """

    use_in_migrations = True

    def _create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The email must be set.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Email-only user (no ``username``), created in Phase 1 so AUTH_USER_MODEL is
    fixed on a fresh DB and never swapped mid-project (avoids the foxugly
    InconsistentMigrationHistory pain, §3.16). Phase 1 only seeds superusers for
    the deck admin; anonymous room participants are NOT users."""

    username = None
    email = models.EmailField(unique=True)
    # Persisted display name for authenticated members (Phase 2) — DISTINCT from
    # the ephemeral anonymous Participant.display_name. Not an auth identifier.
    display_name = models.CharField(max_length=50, blank=True)
    # Email ownership gate (Phase 2 auth). Present now so the shape matches the fleet.
    email_confirmed = models.BooleanField(default=False)
    # Optional profile picture; falls back to deterministic initials in the UI.
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    # Accès offert : accorde tous les droits payants sans souscription Stripe
    # (spec lot A). Distinct de is_staff, qui n'accorde AUCUN droit métier.
    # Lu et court-circuité dans billing/service.py (user_is_paid() / user_quota()),
    # ne doit pas être consulté ailleurs.
    subscription_bypass = models.BooleanField(default=False)
    # Audit seul, aucun effet fonctionnel : pourquoi et quand l'accès a été offert.
    bypass_note = models.CharField(max_length=200, blank=True)
    bypass_granted_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


def generate_magic_token() -> str:
    return secrets.token_urlsafe(32)


class MagicLinkToken(models.Model):
    """Single-use, short-TTL login token keyed on a user (Phase 2 magic-link).
    The raw token travels only in the emailed link; verification consumes it."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="magic_links")
    token = models.CharField(max_length=64, unique=True, default=generate_magic_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def consume(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])


class BypassGrantLog(models.Model):
    """Journal append-only des octrois/revocations d'acces offert (spec lot A-bis).

    Le User porte l'ETAT courant (subscription_bypass / bypass_note / bypass_granted_at) ;
    ce modele porte l'HISTOIRE, y compris l'acteur, que l'etat courant ne dit pas.
    Jamais modifie ni supprime : une revocation ajoute une ligne, elle n'en efface aucune.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="bypass_grants_made",
    )
    # Snapshot de l'email : la trace survit a la suppression du compte staff.
    actor_label = models.CharField(max_length=254, blank=True)
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="bypass_grants_received",
    )
    # Snapshot de l'email : la trace survit a la suppression du compte cible.
    target_label = models.CharField(max_length=254, blank=True)
    granted = models.BooleanField()  # True = octroi, False = revocation
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        verb = "grant" if self.granted else "revoke"
        return f"{verb} {self.target_label} by {self.actor_label}"
