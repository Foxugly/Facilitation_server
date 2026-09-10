"""Email-confirmation flow (send link + confirm). Stateless tokens via Django's
default_token_generator — no extra model. Anti-leak resend."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .mail import send_email

logger = logging.getLogger("facilitation")
User = get_user_model()


def _confirm_link(uidb64: str, token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/auth/confirm-email/{uidb64}/{token}"


def send_confirmation_email(user) -> None:
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = _confirm_link(uidb64, token)
    send_email(
        to=user.email,
        subject="Confirm your Delegation Poker email",
        body=(
            "Welcome to Delegation Poker!\n\n"
            "Confirm your email address to activate your account:\n\n"
            f"{link}\n\n"
            "If you didn't create an account, you can ignore this email.\n"
        ),
    )
    logger.info("email_confirmation_sent", extra={"user_id": user.pk})


def send_duplicate_registration_email(user) -> None:
    send_email(
        to=user.email,
        subject="Someone tried to register with your Delegation Poker email",
        body=(
            "Hello,\n\n"
            "Someone tried to create a Delegation Poker account with this email, but "
            "an account already exists.\n\n"
            "If this was you, just sign in (or use \"forgot password\"). If not, you "
            "can safely ignore this email — no changes were made.\n"
        ),
    )
    logger.info("duplicate_registration_notice_sent", extra={"user_id": user.pk})


def resend_confirmation_email(email: str) -> None:
    user = User.objects.filter(
        email__iexact=(email or "").strip(), is_active=True, email_confirmed=False
    ).first()
    if user:
        send_confirmation_email(user)


def confirm_email(uidb64: str, token: str):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    if not user.is_active or not default_token_generator.check_token(user, token):
        return None
    if not user.email_confirmed:
        user.email_confirmed = True
        user.save(update_fields=["email_confirmed"])
        logger.info("email_confirmed", extra={"user_id": user.pk})
    return user
