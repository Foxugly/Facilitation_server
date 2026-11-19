from django.apps import AppConfig
from django.contrib.admin import apps as admin_apps


class Admin2FAConfig(AppConfig):
    """The app itself: templates, the reset command and the system checks."""

    default = True
    name = "admin_2fa"
    verbose_name = "Admin 2FA"

    def ready(self):
        from . import checks  # registers the system checks (F401: ruff.toml)


class OTPAdminConfig(admin_apps.AdminConfig):
    """Drop-in replacement for ``django.contrib.admin`` in INSTALLED_APPS.

    Same app (label ``admin``, autodiscovery), but ``admin.site`` becomes an
    :class:`~admin_2fa.sites.Admin2FASite`, so every ``@admin.register`` of the
    project lands behind the second factor without touching a single admin.py.
    ``admin_apps.AdminConfig`` is reached through its module on purpose: a bare
    import would make this module declare two default AppConfigs.
    """

    default = False
    default_site = "admin_2fa.sites.Admin2FASite"
