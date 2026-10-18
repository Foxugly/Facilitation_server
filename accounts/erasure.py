"""Account erasure (GDPR, audit 2026-10-18 I6).

What goes with the account, beyond the rows that cascade on the user FK
(memberships, magic links, subscription):
- every refresh token is blacklisted first (their rows only SET_NULL the user);
- the avatar file;
- the room seats keep their votes (results stay meaningful for the team) but
  lose the link to the user and the name, which may be the e-mail address;
- invitations addressed to the account's e-mail.

Teams owned by the user are NOT deleted here: the view refuses with 409 until
ownership is handed over (Team.owner cascades, it would delete the team).
"""
import logging

from django.db import transaction

from .tokens import revoke_sessions

logger = logging.getLogger("facilitation")

ERASED_DISPLAY_NAME = "Deleted user"


def erase_account(user) -> None:
    from rooms.models import Participant
    from teams.models import Invitation

    user_id = user.pk
    avatar = user.avatar.name if user.avatar else None
    storage = user.avatar.storage if user.avatar else None
    with transaction.atomic():
        revoke_sessions(user)
        Participant.objects.filter(user=user).update(user=None, display_name=ERASED_DISPLAY_NAME)
        Invitation.objects.filter(email__iexact=user.email).delete()
        user.delete()
    if avatar:
        storage.delete(avatar)
    logger.info("account_erased", extra={"user_id": user_id})
