"""
Asosiy baza ichida test uchun schema yaratadi (default: test_schema).
Jadvallar test_schema da, public schema (asosiy ma'lumotlar) ta'sirlanmaydi.

Ishlatish:
  python manage.py create_test_db
  python manage.py migrate --settings=core.settings_test_db
  python manage.py test --settings=core.settings_test_db --keepdb
"""
import os

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Asosiy bazada test schema yaratadi (TEST_DB_SCHEMA, default: test_schema). Asosiy bazaga ta'sir qilmaydi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--drop",
            action="store_true",
            help="Agar schema bor bo'lsa, uni o'chirib, qayta yaratadi.",
        )

    def handle(self, *args, **options):
        schema_name = os.environ.get("TEST_DB_SCHEMA", "test_schema")

        with connection.cursor() as cursor:
            if options["drop"]:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                self.stdout.write(
                    self.style.WARNING(f"Schema o'chirildi: {schema_name}")
                )

            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (schema_name,),
            )
            exists = cursor.fetchone()

            if exists and not options["drop"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Schema allaqachon mavjud: {schema_name}. "
                        "Qayta yaratish uchun: python manage.py create_test_db --drop"
                    )
                )
                return

            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
            self.stdout.write(
                self.style.SUCCESS(
                    f"Test schema yaratildi: {schema_name} (asosiy baza ichida). "
                    "Keyin: python manage.py migrate --settings=core.settings_test_db"
                )
            )
