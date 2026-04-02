"""
Test Client yaratadi. Shu raqam bilan login qilganda OTP so'ralmaydi (development va production).
"""
from django.core.management.base import BaseCommand
from django.conf import settings

from users.models.clients import Client


class Command(BaseCommand):
    help = (
        "Test Client yaratadi. TEST_USER_PHONE_NUMBER bilan login qilganda OTP so'ralmaydi. "
        "Raqam .env da TEST_USER_PHONE_NUMBER=+998001234567 ko'rinishida belgilanishi kerak."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone",
            type=str,
            help="Telefon raqami (masalan: +998001234567). Bo'sh bo'lsa TEST_USER_PHONE_NUMBER dan olinadi.",
        )
        parser.add_argument(
            "--first-name",
            type=str,
            default="Test",
            help="Ism (default: Test)",
        )
        parser.add_argument(
            "--last-name",
            type=str,
            default="User",
            help="Familiya (default: User)",
        )

    def handle(self, *args, **options):
        phone = options.get("phone") or settings.TEST_USER_PHONE_NUMBER
        if not phone:
            self.stdout.write(
                self.style.ERROR(
                    "Telefon raqam berilmagan. .env da TEST_USER_PHONE_NUMBER=+998001234567 qo'shing "
                    "yoki --phone=+998001234567 argument bering."
                )
            )
            return

        phone = phone.replace(" ", "").replace("+", "").strip()
        if not phone.startswith("998"):
            phone = "998" + phone
        canonical = "+" + phone

        first_name = options.get("first_name", "Test")
        last_name = options.get("last_name", "User")

        client, created = Client.objects.get_or_create(
            phone_number=canonical,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Test user yaratildi: {client.phone_number} ({client.first_name} {client.last_name})"
                )
            )
            self.stdout.write(
                "Login: POST /api/user/client/login/ → POST /api/user/client/login/verify/ "
                "(OTP bypass: 0000)"
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Test user allaqachon mavjud: {client.phone_number}"
                )
            )
