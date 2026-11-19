"""Lost phone / locked out: reset an account's admin 2FA from a shell.

    manage.py admin_2fa_reset <login>            # delete every OTP device; re-enrol at next admin login
    manage.py admin_2fa_reset <login> --unlock   # keep the devices, only clear the failed-attempt delay

<login> is the value of the user model's USERNAME_FIELD (case-insensitive).
Sessions verified with a deleted device lose their verification on the next request.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp import devices_for_user


class Command(BaseCommand):
    help = "Reset (or just unlock) the admin two-factor authentication of one user."

    def add_arguments(self, parser):
        parser.add_argument("login", help="value of the user model's USERNAME_FIELD (e.g. the email)")
        parser.add_argument(
            "--unlock",
            action="store_true",
            help="keep the devices, only clear the throttling delay after failed codes",
        )

    def handle(self, *args, login, unlock, **options):
        user_model = get_user_model()
        field = user_model.USERNAME_FIELD
        try:
            user = user_model._default_manager.get(**{f"{field}__iexact": login})
        except user_model.DoesNotExist:
            raise CommandError(f"No user with {field}={login!r}.") from None
        except user_model.MultipleObjectsReturned:
            user = user_model._default_manager.get(**{field: login})

        with transaction.atomic():
            devices = list(devices_for_user(user, confirmed=None))
            if unlock:
                for device in devices:
                    if hasattr(device, "throttle_reset"):
                        device.throttle_reset()
                self.stdout.write(self.style.SUCCESS(
                    f"Throttling cleared on {len(devices)} device(s) of {login}; devices kept."
                ))
                return
            for device in devices:
                device.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {len(devices)} OTP device(s) of {login}: they enrol again (QR code) at the next admin login."
        ))
