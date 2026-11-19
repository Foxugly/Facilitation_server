"""Admin 2FA: generic tests, portable as-is (no project-specific model or URL).

The project's own "public API unchanged" test lives next to its API tests.
"""
import re
import time
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.checks import run_checks
from django.core.management import CommandError, call_command
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from admin_2fa.utils import BACKUP_CODE_COUNT, Redacted

PASSWORD = "Adm1n-Passw0rd!"
INDEX = "/admin/"


def _make_user(login, staff=True, superuser=False):
    user_model = get_user_model()
    user = user_model._default_manager.create_user(
        **{user_model.USERNAME_FIELD: login}, password=PASSWORD
    )
    user.is_staff = staff
    user.is_superuser = superuser
    user.save()
    return user


def _login_value(user):
    return getattr(user, get_user_model().USERNAME_FIELD)


def _password_login(client, user, next_url=INDEX):
    return client.post(
        reverse("admin:login"),
        {"username": _login_value(user), "password": PASSWORD, "next": next_url},
    )


def _code(device, offset=0):
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + offset * device.step
    return f"{totp.token():0{device.digits}d}"


def _enrolled_device(user):
    return TOTPDevice.objects.create(user=user, name="admin-2fa", confirmed=True)


def _backup_codes(user, codes=("aaaa2222", "bbbb3333")):
    device = StaticDevice.objects.create(user=user, name="admin-2fa-backup", confirmed=True)
    for code in codes:
        StaticToken.objects.create(device=device, token=code)
    return device


@pytest.fixture
def staff(db):
    return _make_user("staff@example.com", superuser=True)


# --- wiring ---------------------------------------------------------------------


def test_system_checks_pass_with_the_settings_of_this_project(db):
    assert [e for e in run_checks() if e.id.startswith("admin_2fa.")] == []


def test_redacted_hides_the_value_from_tracebacks():
    secret = Redacted(["code1", "code2"])
    assert repr(secret) == "<redacted>"
    assert list(secret) == ["code1", "code2"]
    assert str(Redacted("ABCD")) == "ABCD"
    assert Redacted("<b>").__html__() == "&lt;b&gt;"


# --- enrolment (first admin login) ---------------------------------------------


def test_staff_without_device_is_sent_to_enrolment_and_never_reaches_the_admin(client, staff):
    response = _password_login(client, staff)
    assert response.status_code == 302
    assert response.url.startswith(reverse("admin:2fa_setup"))

    index = client.get(INDEX)
    assert index.status_code == 302
    assert index.url.startswith(reverse("admin:login"))
    # The login page itself forwards the half-logged-in user to the second factor.
    login_page = client.get(index.url)
    assert login_page.status_code == 302
    assert login_page.url.startswith(reverse("admin:2fa_setup"))
    # Any admin page, not just the index.
    assert client.get(reverse("admin:password_change")).status_code == 302


def test_enrolment_page_shows_a_qr_code_and_the_key_and_keeps_the_same_secret(client, staff):
    _password_login(client, staff)
    first = client.get(reverse("admin:2fa_setup"))
    assert first.status_code == 200
    body = first.content.decode()
    assert 'role="img" aria-label="QR code"' in body
    assert not re.search(r"<script(?![^>]*\bsrc=)", body)  # no inline script (CSP)
    assert " style=" not in body.split("<!-- END SVGs -->")[0].split("<body")[1]  # no inline style
    device = TOTPDevice.objects.get(user=staff)
    assert device.confirmed is False
    assert "no-cache" in first["Cache-Control"] and "no-store" in first["Cache-Control"]

    client.get(reverse("admin:2fa_setup"))
    assert TOTPDevice.objects.filter(user=staff).count() == 1  # a reload does not change the QR


def test_enrolment_with_the_right_code_confirms_and_shows_backup_codes_once(client, staff):
    _password_login(client, staff)
    client.get(reverse("admin:2fa_setup"))
    device = TOTPDevice.objects.get(user=staff)

    response = client.post(reverse("admin:2fa_setup"), {"token": _code(device), "next": INDEX})
    assert response.status_code == 200
    device.refresh_from_db()
    assert device.confirmed is True
    tokens = list(StaticToken.objects.filter(device__user=staff).values_list("token", flat=True))
    assert len(tokens) == BACKUP_CODE_COUNT
    body = response.content.decode()
    assert all(token in body for token in tokens)
    assert "no-store" in response["Cache-Control"]

    assert client.get(INDEX).status_code == 200
    # Re-posting (a reload) never shows the codes again.
    again = client.post(reverse("admin:2fa_setup"), {"token": _code(device), "next": INDEX})
    assert again.status_code == 302
    assert not any(token in client.get(INDEX).content.decode() for token in tokens)


def test_enrolment_with_a_wrong_code_is_refused(client, staff):
    _password_login(client, staff)
    client.get(reverse("admin:2fa_setup"))
    device = TOTPDevice.objects.get(user=staff)
    wrong = f"{(int(_code(device)) + 1) % 1_000_000:06d}"

    response = client.post(reverse("admin:2fa_setup"), {"token": wrong})
    assert response.status_code == 200
    assert b"Wrong code" in response.content
    device.refresh_from_db()
    assert device.confirmed is False
    assert not StaticDevice.objects.filter(user=staff).exists()
    assert client.get(INDEX).status_code == 302


def test_enrolled_user_cannot_re_enrol_without_a_reset(client, staff):
    _enrolled_device(staff)
    _password_login(client, staff)
    response = client.get(reverse("admin:2fa_setup"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("admin:2fa_verify"))
    assert TOTPDevice.objects.filter(user=staff).count() == 1


# --- every later login ------------------------------------------------------------


def test_staff_with_a_device_must_give_the_code(client, staff):
    device = _enrolled_device(staff)
    response = _password_login(client, staff, next_url="/admin/auth/group/")
    assert response.status_code == 302
    assert response.url.startswith(reverse("admin:2fa_verify"))
    assert client.get(INDEX).status_code == 302

    bad = client.post(reverse("admin:2fa_verify"), {"token": "000000"})
    assert bad.status_code == 200
    assert client.get(INDEX).status_code == 302

    device.throttle_reset()  # the wrong code above armed the 1 s delay
    good = client.post(reverse("admin:2fa_verify"), {"token": _code(device), "next": "/admin/auth/group/"})
    assert good.status_code == 302
    assert good.url == "/admin/auth/group/"
    assert client.get(INDEX).status_code == 200


def test_wrong_codes_are_throttled_by_django_otp(client, staff):
    device = _enrolled_device(staff)
    _password_login(client, staff)
    client.post(reverse("admin:2fa_verify"), {"token": "000000"})
    device.refresh_from_db()
    assert device.throttling_failure_count == 1

    # Even the right code is refused while the delay runs.
    response = client.post(reverse("admin:2fa_verify"), {"token": _code(device)})
    assert response.status_code == 200
    assert b"Too many failed attempts" in response.content
    assert client.get(INDEX).status_code == 302


def test_backup_code_works_exactly_once(client, staff):
    _enrolled_device(staff)
    _backup_codes(staff)

    _password_login(client, staff)
    used = client.post(reverse("admin:2fa_verify"), {"token": "AAAA 2222"})  # spaces and caps tolerated
    assert used.status_code == 302
    assert client.get(INDEX).status_code == 200
    assert not StaticToken.objects.filter(token="aaaa2222").exists()

    client.logout()
    other = client.__class__()
    _password_login(other, staff)
    reused = other.post(reverse("admin:2fa_verify"), {"token": "aaaa2222"})
    assert reused.status_code == 200
    assert b"Invalid code" in reused.content
    assert other.get(INDEX).status_code == 302


def test_next_is_never_an_external_url(client, staff):
    device = _enrolled_device(staff)
    _password_login(client, staff)
    response = client.post(
        reverse("admin:2fa_verify"), {"token": _code(device), "next": "https://evil.example/"}
    )
    assert response.status_code == 302
    assert response.url == INDEX


# --- who may reach the 2FA pages -------------------------------------------------


def test_non_staff_gets_no_admin_access(client, db):
    user = _make_user("user@example.com", staff=False)
    response = _password_login(client, user)
    assert response.status_code == 200  # the login form again, with its error
    assert not response.wsgi_request.user.is_authenticated
    client.force_login(user)
    for url in (INDEX, reverse("admin:2fa_setup"), reverse("admin:2fa_verify")):
        response = client.get(url)
        assert response.status_code == 302
        assert response.url.startswith(reverse("admin:login"))
    assert not TOTPDevice.objects.filter(user=user).exists()


def test_anonymous_is_sent_to_the_password_login(client, db):
    for url in (reverse("admin:2fa_setup"), reverse("admin:2fa_verify")):
        response = client.get(url)
        assert response.status_code == 302
        assert response.url.startswith(reverse("admin:login"))


def test_post_without_csrf_token_is_refused(staff):
    from django.test import Client

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(staff)
    response = csrf_client.post(reverse("admin:2fa_setup"), {"token": "123456"})
    assert response.status_code == 403


# --- reset command ------------------------------------------------------------------


def test_reset_command_deletes_the_devices_and_forces_a_new_enrolment(client, staff):
    device = _enrolled_device(staff)
    _backup_codes(staff)
    _password_login(client, staff)
    client.post(reverse("admin:2fa_verify"), {"token": _code(device)})
    assert client.get(INDEX).status_code == 200

    out = StringIO()
    call_command("admin_2fa_reset", _login_value(staff).upper(), stdout=out)
    assert "Deleted 2 OTP device(s)" in out.getvalue()
    assert not TOTPDevice.objects.filter(user=staff).exists()
    assert not StaticDevice.objects.filter(user=staff).exists()

    # The session verified with the deleted device is no longer verified.
    response = client.get(INDEX)
    assert response.status_code == 302
    assert client.get(response.url).url.startswith(reverse("admin:2fa_setup"))


def test_reset_command_unlock_keeps_the_devices(staff):
    device = _enrolled_device(staff)
    device.throttle_increment()
    call_command("admin_2fa_reset", _login_value(staff), "--unlock", stdout=StringIO())
    device.refresh_from_db()
    assert device.throttling_failure_count == 0
    assert TOTPDevice.objects.filter(user=staff).exists()


def test_reset_command_unknown_user(db):
    with pytest.raises(CommandError):
        call_command("admin_2fa_reset", "nobody@example.com", stdout=StringIO())


def test_force_admin_login_helper_opens_the_admin(client, staff):
    from admin_2fa.testing import force_admin_login

    force_admin_login(client, staff)
    assert client.get(INDEX).status_code == 200


def test_system_checks_catch_a_half_done_port(settings):
    from admin_2fa.checks import check_admin_2fa

    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "django_otp" not in m]
    settings.OTP_TOTP_THROTTLE_FACTOR = 0
    assert {e.id for e in check_admin_2fa(None)} == {"admin_2fa.E002", "admin_2fa.E004"}
