"""Set an admin's sign-in password.

Admin accounts created before per-account passwords have no hash stored, and login now
refuses those rather than falling back to a shared secret. This is how they are migrated:

    python manage.py set_admin_password admin@weel.uz
    python manage.py set_admin_password admin@weel.uz --password '…'   # non-interactive

Once every admin has one, remove ADMIN_LOGIN_PASSWORD from the environment.
"""

from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.admin_auth.raw_repository import get_active_admin_by_email, set_admin_password

MIN_LENGTH = 10


class Command(BaseCommand):
    help = "Set the password for an existing admin account."

    def add_arguments(self, parser):
        parser.add_argument("email", help="The admin's email address.")
        parser.add_argument(
            "--password",
            help="The new password. Omit to be prompted, which keeps it out of your shell history.",
        )

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        user = get_active_admin_by_email(email)
        if not user:
            raise CommandError(f"No active admin with the email {email}.")

        password = options.get("password")
        if not password:
            password = getpass("New password: ")
            if password != getpass("Repeat: "):
                raise CommandError("The two entries did not match.")

        if len(password) < MIN_LENGTH:
            raise CommandError(f"Use at least {MIN_LENGTH} characters.")

        if not set_admin_password(user.id, password):
            raise CommandError("Could not update the account.")

        self.stdout.write(self.style.SUCCESS(f"Password set for {email} (id {user.id})."))
