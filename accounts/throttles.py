from rest_framework.permissions import SAFE_METHODS
from rest_framework.settings import api_settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class RegisterRateThrottle(AnonRateThrottle):
    scope = "register"


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


class ResendEmailRateThrottle(AnonRateThrottle):
    scope = "resend"


class MagicLinkRateThrottle(AnonRateThrottle):
    scope = "magic_link"


class _WriteScopedUserThrottle(UserRateThrottle):
    """Per-user (per-IP when anonymous) scoped rate that counts writes only.

    Scoped to the costly side effect (an e-mail sent, a file stored), so reading
    the same endpoint (e.g. the pending invitations list) does not use it up.
    """

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


class InviteRateThrottle(_WriteScopedUserThrottle):
    scope = "invite"


class HistoryEmailRateThrottle(_WriteScopedUserThrottle):
    scope = "history_email"


class UploadRateThrottle(_WriteScopedUserThrottle):
    scope = "upload"


class AccountDeleteRateThrottle(_WriteScopedUserThrottle):
    scope = "account_delete"


def with_default_throttles(*extra):
    """The default throttles plus ``extra``: a view's ``throttle_classes`` replaces
    the defaults, and the user/anon rates must keep applying."""
    return [*api_settings.DEFAULT_THROTTLE_CLASSES, *extra]
