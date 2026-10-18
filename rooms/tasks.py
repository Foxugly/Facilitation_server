from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Room


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
