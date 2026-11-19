"""Test helper for projects using admin_2fa: log a staff user into the admin past 2FA.

    from admin_2fa.testing import force_admin_login
    force_admin_login(client, user)   # instead of client.force_login(user) for /admin/ tests
"""
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from .utils import TOTP_DEVICE_NAME


def force_admin_login(client, user):
    client.force_login(user)
    device = TOTPDevice.objects.filter(user=user, confirmed=True).first() or TOTPDevice.objects.create(
        user=user, name=TOTP_DEVICE_NAME, confirmed=True
    )
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return device
