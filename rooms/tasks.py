import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Result, Room, Round

logger = logging.getLogger("facilitation")


@shared_task
def expire_stale_rooms():
    """Mark rooms inactive for > ROOM_INACTIVITY_HOURS as expired (scope §4).

    Belt-and-braces cleanup: join / WS entry already refuse an expired room
    lazily (Room.is_live); this beat task just flips the flag in the background.
    """
    now = timezone.now()
    count = Room.objects.filter(
        is_expired=False, team__isnull=True, expires_at__lt=now
    ).update(is_expired=True)
    # Team rooms skip the 8h window but close after a long inactivity (Room.is_live).
    team_cutoff = now - timezone.timedelta(days=settings.TEAM_ROOM_INACTIVITY_DAYS)
    count += Room.objects.filter(
        is_expired=False, team__isnull=False, last_activity_at__lt=team_cutoff
    ).update(is_expired=True)
    return count


@shared_task
def purge_retention():
    """Daily retention purge (GDPR, audit 2026-10-18 I6).

    - Anonymous rooms are deleted RETENTION_ROOM_DAYS after their last activity,
      with their participants (display names), rounds, items, responses and
      results. Team rooms are kept: they ARE the team history, and go with the
      team (Room.team cascades).
    - Magic links past their expiry, refresh-token rows past theirs (simplejwt
      blacklist bookkeeping) and invitations expired for
      RETENTION_INVITATION_DAYS (each carries an e-mail address).
    """
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

    from accounts.models import MagicLinkToken
    from teams.models import Invitation

    now = timezone.now()
    room_cutoff = now - timezone.timedelta(days=settings.RETENTION_ROOM_DAYS)
    old_rooms = Room.objects.filter(team__isnull=True, last_activity_at__lt=room_cutoff)
    # Result.item is PROTECT: drop the current-round pointer and the results
    # first, then the rounds (items, responses), then the rooms (participants).
    old_rooms.update(current_round=None)
    Result.objects.filter(round__room__in=old_rooms).delete()
    Round.objects.filter(room__in=old_rooms).delete()
    counts = {"rooms": old_rooms.delete()[1].get("rooms.Room", 0)}
    counts["magic_links"] = MagicLinkToken.objects.filter(expires_at__lt=now).delete()[0]
    counts["refresh_tokens"] = OutstandingToken.objects.filter(expires_at__lt=now).delete()[0]
    invitation_cutoff = now - timezone.timedelta(days=settings.RETENTION_INVITATION_DAYS)
    counts["invitations"] = Invitation.objects.filter(expires_at__lt=invitation_cutoff).delete()[0]
    logger.info("retention_purge", extra=counts)
    return counts
