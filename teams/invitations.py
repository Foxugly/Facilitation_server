"""Team invitation email. The link requires the invitee to be signed in to accept
(login-required, scope §4.1)."""
import logging

from django.conf import settings

from accounts.mail import send_email

logger = logging.getLogger("facilitation")


def send_invitation_email(invitation) -> None:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    link = f"{base}/teams/join/{invitation.token}"
    send_email(
        to=invitation.email,
        subject=f"You've been invited to a team on Delegation Poker",
        body=(
            f"You've been invited to join the team \"{invitation.team.name}\" on "
            "Delegation Poker.\n\n"
            "Sign in (or create an account with this email) and open the link below "
            "to accept:\n\n"
            f"{link}\n\n"
            "If you didn't expect this, you can ignore this email.\n"
        ),
    )
    logger.info("team_invitation_sent", extra={"team_id": invitation.team_id, "email": invitation.email})
