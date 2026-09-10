"""Transactional email helper. Uses Microsoft Graph in prod (fleet standard, when
GRAPH_* is configured); otherwise Django's EMAIL_BACKEND (console in dev). Best-effort
— a send failure never breaks the request flow."""
import logging

from django.conf import settings
from django.core.mail import send_mail

from . import graph_mail

logger = logging.getLogger("facilitation")


def send_email(*, to: str, subject: str, body: str) -> None:
    try:
        if graph_mail.is_configured():
            graph_mail.send_email(to=to, subject=subject, body=body)
        else:
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@facilitation.foxugly.com")
            send_mail(subject, body, from_email, [to], fail_silently=False)
        logger.info("email_sent", extra={"to": to, "subject": subject})
    except Exception:
        logger.exception("email_send_failed", extra={"to": to})
