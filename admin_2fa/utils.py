BACKUP_CODE_COUNT = 10
TOTP_DEVICE_NAME = "admin-2fa"
STATIC_DEVICE_NAME = "admin-2fa-backup"


def is_verified(user):
    """True when OTPMiddleware has bound a confirmed device to this session.

    ``is_verified`` only exists once OTPMiddleware wrapped ``request.user``; a
    user object just set by ``auth.login()`` (or a DRF/JWT user) has none and
    counts as not verified.
    """
    check = getattr(user, "is_verified", None)
    return bool(callable(check) and check())


def needs_otp(user):
    """Password-authenticated staff that still owes the second factor."""
    return bool(
        user.is_authenticated and user.is_active and user.is_staff and not is_verified(user)
    )


class Redacted:
    """Wraps a secret so it renders in templates but never in a traceback.

    Sentry (and Django's error reports) serialize frame locals with ``repr()``;
    templates use ``str()`` / ``__html__`` / iteration. The TOTP key, the QR code
    and the backup codes only ever live in view locals inside this wrapper.
    Deliberately not a ``Sequence``: Sentry would then serialize the items.
    """

    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value

    def __str__(self):
        return str(self._value)

    def __html__(self):
        html = getattr(self._value, "__html__", None)
        if html is not None:
            return html()
        from django.utils.html import escape

        return escape(str(self._value))

    def __iter__(self):
        return iter(self._value)

    def __repr__(self):
        return "<redacted>"
