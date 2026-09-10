"""Magic-link login (Phase 2): single-use, short-TTL token emailed to the user.

Request is anti-leak (always the same response); verify consumes the token and
returns the user. Only confirmed, active accounts get a link."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .mail import send_email
from .models import MagicLinkToken

logger = logging.getLogger("facilitation")
User = get_user_model()


def _link(token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/auth/magic-link/{token}"


def request_magic_link(email: str) -> None:
    user = User.objects.filter(
        email__iexact=(email or "").strip(), is_active=True, email_confirmed=True
    ).first()
    if not user:
        return
    ttl_minutes = getattr(settings, "MAGIC_LINK_TTL_MINUTES", 15)
    link_token = MagicLinkToken.objects.create(
        user=user, expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes)
    )
    send_email(
        to=user.email,
        subject="Your Delegation Poker sign-in link",
        body=(
            "Use the link below to sign in (valid for "
            f"{ttl_minutes} minutes, single use):\n\n"
            f"{_link(link_token.token)}\n\n"
            "If you didn't request this, you can ignore this email.\n"
        ),
    )
    logger.info("magic_link_sent", extra={"user_id": user.pk})


def verify_magic_link(token: str):
    """Consume the token and return the user, or None if invalid/expired/used."""
    link_token = MagicLinkToken.objects.select_related("user").filter(token=token or "").first()
    if link_token is None or not link_token.is_valid:
        return None
    link_token.consume()
    return link_token.user
