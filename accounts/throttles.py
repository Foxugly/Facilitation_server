from rest_framework.throttling import AnonRateThrottle


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
