"""
Test Partner yaratadi. Shu raqam bilan login qilganda OTP so'ralmaydi.
TEST_PARTNER_PHONE_NUMBER .env da bo'lishi kerak.
"""
from django.core.management.base import BaseCommand
from django.conf import settings

from django.utils import timezone
from users.models.logs import SmsPurpose
from users.services import OTPRedisService

from users.raw_repository import (
    create_partner,
    exists_partner_username,
    get_active_user_by_phone,
)


class Command(BaseCommand):
    help = (
        "Test Partner yaratadi. TEST_PARTNER_PHONE_NUMBER bilan login qilganda OTP so'ralmaydi. "
        "Raqam .env da TEST_PARTNER_PHONE_NUMBER=+998901234568 ko'rinishida belgilanishi kerak."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone",
            type=str,
            help="Telefon raqami (masalan: +998901234568). Bo'sh bo'lsa TEST_PARTNER_PHONE_NUMBER dan olinadi.",
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
            default="Partner",
            help="Familiya (default: Partner)",
        )
        parser.add_argument(
            "--username",
            type=str,
            default=None,
            help="Username (default: test_partner_<phone>). Unique bo'lishi kerak.",
        )
        parser.add_argument(
            "--email",
            type=str,
            default="",
            help="Email (ixtiyoriy, default: bo'sh).",
        )

    def handle(self, *args, **options):
        phone = options.get("phone") or settings.TEST_PARTNER_PHONE_NUMBER
        if not phone:
            self.stdout.write(
                self.style.ERROR(
                    "Telefon raqam berilmagan. .env da TEST_PARTNER_PHONE_NUMBER=+998901234568 qo'shing "
                    "yoki --phone=+998901234568 argument bering."
                )
            )
            return

        phone = phone.replace(" ", "").replace("+", "").strip()
        if not phone.startswith("998"):
            phone = "998" + phone
        canonical = "+" + phone

        first_name = options.get("first_name", "Test")
        last_name = options.get("last_name", "Partner")
        username = options.get("username") or f"test_partner_{phone}"
        email = (options.get("email") or "").strip() or None

        partner = get_active_user_by_phone(canonical, role="partner")
        created = False
        if partner is None:
            if exists_partner_username(username):
                username = f"{username}_{int(timezone.now().timestamp())}"
            partner = create_partner(
                phone_number=canonical,
                username=username,
                first_name=first_name,
                last_name=last_name,
                email=email,
            )
            created = True

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Test partner yaratildi: {partner.phone_number} "
                    f"({partner.first_name} {partner.last_name}, @{partner.username})"
                )
            )
            self.stdout.write(
                "Login: POST /api/user/partner/login/ → POST /api/user/partner/login/verify/ "
                f"(OTP bypass: {OTPRedisService.test_bypass_otp(SmsPurpose.PARTNER_LOGIN)})"
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Test partner allaqachon mavjud: {partner.phone_number}"
                )
            )
