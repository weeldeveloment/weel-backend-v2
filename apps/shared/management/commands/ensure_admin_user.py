import os

from django.core.management.base import BaseCommand

from admin_auth.raw_repository import (
    create_admin_user,
    exists_admin_email,
    make_unique_admin_username,
)


class Command(BaseCommand):
    help = "ADMIN_ALLOWED_EMAIL (yoki --email) bo'yicha admin user yo'q bo'lsa yaratadi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Agar berilsa, ADMIN_ALLOWED_EMAIL o'rniga ishlatiladi.",
        )
        parser.add_argument(
            "--first-name",
            type=str,
            default="Admin",
        )
        parser.add_argument(
            "--last-name",
            type=str,
            default="",
        )

    def handle(self, *args, **options):
        email = (options.get("email") or os.getenv("ADMIN_ALLOWED_EMAIL") or "").strip()
        if not email:
            self.stdout.write(
                self.style.ERROR(
                    "Email berilmagan. .env da ADMIN_ALLOWED_EMAIL=... qo'shing yoki --email=... bering."
                )
            )
            return

        if exists_admin_email(email):
            self.stdout.write(self.style.WARNING(f"Admin allaqachon mavjud: {email}"))
            return

        base = (email.split("@")[0] or "admin").strip()
        username = make_unique_admin_username(base)
        create_admin_user(
            email=email,
            username=username,
            first_name=options.get("first_name") or "Admin",
            last_name=options.get("last_name") or "",
        )
        self.stdout.write(self.style.SUCCESS(f"Admin yaratildi: {email} (username={username})"))
