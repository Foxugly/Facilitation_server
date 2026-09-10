"""Password-reset flow (request link + confirm). Stateless Django tokens; anti-leak."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .mail import send_email

logger = logging.getLogger("facilitation")
User = get_user_model()


def _reset_link(uidb64: str, token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/auth/reset-password/{uidb64}/{token}"


def request_password_reset(email: str) -> None:
    """Send a reset link if the email matches an active account (silent otherwise)."""
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    if not user:
        return
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    send_email(
        to=user.email,
        subject="Reset your Delegation Poker password",
        body=(
            "You (or someone) requested a password reset. Open the link below to set "
            "a new password:\n\n"
            f"{_reset_link(uidb64, token)}\n\n"
            "If you didn't request this, you can ignore this email.\n"
        ),
    )
    logger.info("password_reset_sent", extra={"user_id": user.pk})


def confirm_password_reset(uidb64: str, token: str, new_password: str) -> bool:
    """Validate the link + set the new password. Raises DjangoValidationError on a
    weak password; returns False on an invalid/expired link, True on success."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return False
    if not user.is_active or not default_token_generator.check_token(user, token):
        return False
    validate_password(new_password, user=user)
    user.set_password(new_password)
    # A successful reset also confirms email ownership.
    if not user.email_confirmed:
        user.email_confirmed = True
    user.save()
    logger.info("password_reset_done", extra={"user_id": user.pk})
    return True
