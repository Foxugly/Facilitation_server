"""Token generators and session revocation (audit 2026-10-18, I3 + I4).

One generator per purpose (OPERATIONS.md §3.25): password reset keeps Django's
``default_token_generator``; e-mail confirmation gets its own salt and hashes
``email_confirmed`` and ``email``, so a confirmation link stops validating once
the account is confirmed and is never accepted as a reset key (nor the reverse).
"""
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils import timezone


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "accounts.tokens.EmailConfirmationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{super()._make_hash_value(user, timestamp)}{user.email_confirmed}{user.email}"


email_confirmation_token_generator = EmailConfirmationTokenGenerator()


def revoke_sessions(user) -> None:
    """Blacklist every refresh token of ``user`` and burn their pending magic links.

    Called when the password changes hands (reset): a stolen refresh token or a
    sign-in link already in someone else's mailbox must not survive it.
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    from .models import MagicLinkToken

    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)
    MagicLinkToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
