"""System checks: `manage.py check` lists every missing piece when porting."""
from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Tags, register

REQUIRED_APPS = ("django_otp", "django_otp.plugins.otp_totp", "django_otp.plugins.otp_static")
AUTH_MW = "django.contrib.auth.middleware.AuthenticationMiddleware"
OTP_MW = "django_otp.middleware.OTPMiddleware"


@register(Tags.security)
def check_admin_2fa(app_configs, **kwargs):
    errors = []
    for app in REQUIRED_APPS:
        if not apps.is_installed(app):
            errors.append(Error(f"admin_2fa needs {app!r} in INSTALLED_APPS.", id="admin_2fa.E001"))

    middleware = list(settings.MIDDLEWARE)
    if OTP_MW not in middleware or AUTH_MW not in middleware or middleware.index(OTP_MW) < middleware.index(AUTH_MW):
        errors.append(Error(
            f"{OTP_MW} must be in MIDDLEWARE, after {AUTH_MW}.",
            id="admin_2fa.E002",
        ))

    from django.contrib import admin

    from .sites import Admin2FASite

    if not isinstance(admin.site, Admin2FASite):
        errors.append(Error(
            "admin.site is not the 2FA site: the admin is reachable with a password only.",
            hint='Replace "django.contrib.admin" with "admin_2fa.apps.OTPAdminConfig" in INSTALLED_APPS.',
            id="admin_2fa.E003",
        ))

    for name in ("OTP_TOTP_THROTTLE_FACTOR", "OTP_STATIC_THROTTLE_FACTOR"):
        if getattr(settings, name, 1) <= 0:
            errors.append(Error(
                f"{name} <= 0 disables django-otp's brute-force throttling.",
                id="admin_2fa.E004",
            ))
    return errors
