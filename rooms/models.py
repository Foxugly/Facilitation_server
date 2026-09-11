"""Runtime models for a room (data-model spec §5).

Identity is a per-participant secret ``token`` (spec P5); the deck is a frozen
``deck_snapshot`` JSON on the room (spec §4); a ``Round`` is one activity run in
the room, its ``state`` running idle → open → revealed → acted (spec §5.4).

``Round`` was called ``VoteSession`` until the Facilitation fork: "session" is
banned from the domain vocabulary because it collided with that former name.
The WebSocket message type ``session.join`` is NOT part of that rename — it is
the wire contract (§4) and renaming it would break the SPA.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from teams.models import ResultLayout


class Role(models.TextChoices):
    FACILITATOR = "facilitator", "Facilitator"
    VOTER = "voter", "Voter"


class RoundState(models.TextChoices):
    IDLE = "idle", "Idle"
    OPEN = "open", "Open"
    REVEALED = "revealed", "Revealed"
    ACTED = "acted", "Acted"


class Room(models.Model):
    code = models.CharField(max_length=8, unique=True)  # UPPER, ambiguous chars excluded
    title = models.CharField(max_length=120, blank=True)
    vote_type = models.ForeignKey("decks.VoteType", on_delete=models.PROTECT)
    # The deck currently in play. Frozen from the referential at creation; it only
    # ever changes by swapping in another entry of ``deck_snapshots`` (never edited).
    deck_snapshot = models.JSONField()
    # Every deck this room may play, frozen at creation (the team's enabled poker
    # types). Self-sufficient like deck_snapshot — the runtime never reads ``decks``.
    deck_snapshots = models.JSONField(default=list, blank=True)
    current_round = models.ForeignKey(
        "rooms.Round", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Phase 2: a room tied to a team is members-only and NON-ephemeral (no 8h expiry).
    # Null = free anonymous room (Phase 1 default).
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, null=True, blank=True, related_name="rooms")
    # Hard cap on participants per room (product limit).
    # Fige a la creation depuis ROOM_MAX_PARTICIPANTS : une salle deja ouverte garde
    # sa limite si le reglage change, sans quoi elle pourrait se retrouver au-dela.
    max_participants = models.PositiveSmallIntegerField(default=15)
    # Comment le depouillement s'affiche a la place de la main. Fige depuis l'equipe a
    # la creation, comme la limite ci-dessus : une salle en cours ne doit pas changer
    # de mise en page sous les yeux des joueurs parce qu'un manager a bascule le
    # reglage ailleurs. Une salle anonyme n'a pas d'equipe et garde donc la valeur
    # par defaut.
    result_layout = models.CharField(max_length=8, choices=ResultLayout.choices, default=ResultLayout.CARDS)
    # Timer de round (optionnel) : le facilitateur l'active et regle sa duree.
    # Porte par la room et non par le round, pour persister d'un round a l'autre.
    # Duree bornee 10-60 s par pas de 5, normalisee cote serveur (services.set_timer).
    timer_enabled = models.BooleanField(default=False)
    timer_seconds = models.PositiveSmallIntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    is_expired = models.BooleanField(default=False)

    def touch(self, *, save=True):
        """Slide the 8h inactivity window forward (scope §4). Team rooms don't expire."""
        now = timezone.now()
        self.last_activity_at = now
        self.expires_at = now + timezone.timedelta(hours=settings.ROOM_INACTIVITY_HOURS)
        if save:
            self.save(update_fields=["last_activity_at", "expires_at"])

    @property
    def is_live(self):
        if self.is_expired:
            return False
        return self.team_id is not None or self.expires_at > timezone.now()

    def __str__(self):
        return self.code


class Participant(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="participants")
    token = models.CharField(max_length=64, unique=True)  # secret, replayed on each WS (re)connect
    public_id = models.UUIDField(default=uuid.uuid4, editable=False)  # broadcast id (≠ token)
    display_name = models.CharField(max_length=50)  # ephemeral display name, NOT an auth identifier
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.VOTER)
    is_connected = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(  # Phase 2 (authenticated member); nullable now
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("room", "public_id"), name="uniq_participant_room_pubid"),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.role})"


class Item(models.Model):
    """Un sujet manipule par une activite : un point d'agenda pose par le
    facilitateur, ou un post-it ecrit par un participant.

    Porte par le ROUND (design 2026-09-11 §3) : les items d'un round passe ne
    bougent plus, meme si le meme sujet est rejoue ou reformule plus tard.
    """

    round = models.ForeignKey("rooms.Round", on_delete=models.CASCADE, related_name="items")
    text = models.CharField(max_length=300)
    sequence = models.PositiveSmallIntegerField(default=1)
    # Qui l'a ecrit. Null quand le facilitateur pose un sujet au nom de la salle.
    # L'anonymat d'un brainstorming est une politique d'AFFICHAGE cote serveur
    # (on n'emet pas la cle), jamais un champ vide en base — meme regle que
    # Vote.participant.
    author = models.ForeignKey(
        Participant, on_delete=models.SET_NULL, null=True, blank=True, related_name="items"
    )
    # L'item dont celui-ci est la copie, quand une activite reprend la sortie de
    # la precedente (chainage, livraison 5e). Copie et NON reference : reformuler
    # ici ne doit pas reecrire l'historique de l'activite source.
    origin_item = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="copies"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("round", "sequence", "id")

    def __str__(self):
        return self.text


class Round(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="rounds")
    state = models.CharField(max_length=10, choices=RoundState.choices, default=RoundState.IDLE)
    facilitator = models.ForeignKey(
        Participant, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # The deck this round was played with, frozen when the round starts. The room's
    # active deck can change between rounds, so results must not be relabelled by a
    # later switch (history maps chosen_value -> label through this).
    deck_snapshot = models.JSONField(null=True, blank=True)
    # Reveal mode, chosen by the facilitator and frozen when the round opens.
    # Nominative by default (who voted what); anonymous hides the participant->card
    # link entirely and is a paid-team option. Voters see the mode BEFORE voting —
    # switching it after votes are in would expose people who thought otherwise.
    is_anonymous = models.BooleanField(default=False)
    opened_at = models.DateTimeField(null=True, blank=True)
    revealed_at = models.DateTimeField(null=True, blank=True)
    # Echeance du vote, posee a l'ouverture quand le timer est actif. Le serveur
    # fait autorite : le decompte affiche par le client est cosmetique.
    vote_deadline = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Round<{self.pk}> {self.state}"


class Vote(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="votes")
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="votes")
    card_value = models.CharField(max_length=32)  # ∈ snapshot cards[].value; secret until reveal
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("round", "participant"), name="uniq_vote_round_participant"),
        ]

    def __str__(self):
        return f"Vote<{self.pk}> p={self.participant_id}"


class Result(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="results")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="results")
    chosen_value = models.CharField(max_length=32)
    decided_by = models.ForeignKey(Participant, on_delete=models.SET_NULL, null=True, blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("round", "item"), name="uniq_result_round_item"),
        ]

    def __str__(self):
        return f"Result<{self.pk}> {self.chosen_value}"
