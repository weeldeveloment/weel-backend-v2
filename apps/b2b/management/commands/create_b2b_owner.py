"""
B2B company va owner user yaratadi.
Login: POST /api/b2b/auth/login/ → POST /api/b2b/auth/login/verify/
Test rejimida OTP: 0000 (TEST_B2B_PHONE_NUMBER .env da bo'lsa)
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.b2b.repository import create_b2b_user, create_company, get_b2b_user_by_phone
from apps.b2b.models import B2BUserRole
from shared.raw.db import fetch_one


def _get_company_by_name(name: str):
    return fetch_one("SELECT * FROM b2b_company WHERE name = %s AND is_active = TRUE", [name])


class Command(BaseCommand):
    help = "B2B company va owner user yaratadi. Login uchun /api/b2b/auth/login/ endpointidan foydalaning."

    def add_arguments(self, parser):
        parser.add_argument("--phone", type=str, required=True, help="Owner telefon raqami (masalan: +998901234567)")
        parser.add_argument("--company", type=str, default="Test Company", help="Kompaniya nomi (default: Test Company)")
        parser.add_argument("--first-name", type=str, default="Owner", help="Ism")
        parser.add_argument("--last-name", type=str, default="Test", help="Familiya")

    def handle(self, *args, **options):
        phone = options["phone"].replace(" ", "").strip()
        if not phone.startswith("+"):
            phone = "+" + phone

        company_name = options["company"]
        first_name = options["first_name"]
        last_name = options["last_name"]

        existing_user = get_b2b_user_by_phone(phone)
        if existing_user:
            self.stdout.write(
                self.style.WARNING(
                    f"Bu telefon raqam bilan B2B user allaqachon mavjud:\n"
                    f"  ID: {existing_user['id']}\n"
                    f"  Company ID: {existing_user['company_id']}\n"
                    f"  Role: {existing_user['role']}\n"
                    f"  Phone: {existing_user['phone']}"
                )
            )
            return

        company = _get_company_by_name(company_name)
        if company:
            self.stdout.write(self.style.WARNING(f"Kompaniya allaqachon mavjud: {company_name} (ID: {company['id']})"))
        else:
            company = create_company(name=company_name)
            if not company:
                self.stdout.write(self.style.ERROR("Kompaniya yaratishda xatolik!"))
                return
            self.stdout.write(self.style.SUCCESS(f"Kompaniya yaratildi: {company_name} (ID: {company['id']})"))

        user = create_b2b_user(
            company_id=company["id"],
            phone=phone,
            first_name=first_name,
            last_name=last_name,
            role=B2BUserRole.OWNER,
        )

        if not user:
            self.stdout.write(self.style.ERROR("User yaratishda xatolik!"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"\nOwner user muvaffaqiyatli yaratildi!\n"
            f"  ID:         {user['id']}\n"
            f"  Phone:      {user['phone']}\n"
            f"  Name:       {user.get('first_name')} {user.get('last_name')}\n"
            f"  Role:       {user.get('role')}\n"
            f"  Company:    {company_name} (ID: {company['id']})\n"
            f"\nLogin qilish:\n"
            f"  1. POST /api/b2b/auth/login/         → {{\"phone\": \"{phone}\"}}\n"
            f"  2. POST /api/b2b/auth/login/verify/  → {{\"phone\": \"{phone}\", \"otp\": \"<sms kod>\"}}\n"
            f"\n  TEST_B2B_PHONE_NUMBER={phone} bo'lsa OTP: 0000"
        ))
