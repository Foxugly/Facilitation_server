"""Mandatory TOTP two-factor authentication for the Django admin (fleet reference).

Self-contained: copy this package as-is into another Django project, then follow
the settings in its README section (INSTALLED_APPS, MIDDLEWARE, OTP_TOTP_ISSUER).
`manage.py check` tells you what is missing (system checks ``admin_2fa.E00x``).

Scope: ``/admin/`` only. API users (JWT, DRF) never see it.
"""
