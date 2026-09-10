"""History-of-the-day email to every team member (login required to open the link).

Synchronous best-effort like the team invitation email — proven on the fleet and avoids
depending on a running Celery worker. Copy is English, consistent with the other
transactional emails; per-user language is not stored.
"""
import logging

from django.conf import settings

from accounts.mail import send_email

logger = logging.getLogger("facilitation")


def _plain_level(level_name):
    """Resolve an entry's levelName (i18n dict or raw value) to a single string for the email."""
    if isinstance(level_name, dict):
        return level_name.get("en") or next(iter(level_name.values()), "")
    return str(level_name)


def send_history_email(team, day, entries) -> int:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    link = f"{base}/history/{team.id}/{day.isoformat()}"
    lines = "\n".join(f"- {e['subject']}: {_plain_level(e['levelName'])}" for e in entries)
    body = (
        f"Delegation Poker — results for the team \"{team.name}\" on {day.isoformat()}:\n\n"
        f"{lines}\n\n"
        "Sign in and open the link below to see the full history:\n\n"
        f"{link}\n"
    )
    subject = f"Delegation Poker — {team.name} results ({day.isoformat()})"

    recipients = {
        m.user.email
        for m in team.memberships.all()
        if getattr(m.user, "email", "")
    }
    for email in recipients:
        send_email(to=email, subject=subject, body=body)
    logger.info("history_email_sent", extra={"team_id": team.id, "day": day.isoformat(), "count": len(recipients)})
    return len(recipients)
