"""
Barcha jadval ma'lumotlarini o'chiradi, faqat jadval strukturalari va
django_migrations jadvalidagi migration yozuvlari qoladi.
"""
from django.core.management.base import BaseCommand
from django.db import connection


# O'chirilmaydigan jadvallar (migration tarixi saqlanadi)
EXCLUDE_TABLES = {"django_migrations"}


class Command(BaseCommand):
    help = (
        "PostgreSQL dagi barcha ma'lumotlarni o'chiradi. "
        "Jadval strukturalari va django_migrations qoladi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Tasdiq so'ramasdan bajaradi.",
        )

    def handle(self, *args, **options):
        if not options["no_input"]:
            confirm = input(
                "Barcha ma'lumotlar o'chiriladi (django_migrations bundan mustasno). "
                "Davom etasizmi? [y/N]: "
            )
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("Bekor qilindi."))
                return

        with connection.cursor() as cursor:
            # PostgreSQL: public schema dagi barcha jadvallar
            cursor.execute("""
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename;
            """)
            tables = [row[0] for row in cursor.fetchall()]

            to_truncate = [t for t in tables if t not in EXCLUDE_TABLES]
            if not to_truncate:
                self.stdout.write(self.style.WARNING("O'chiriladigan jadval yo'q."))
                return

            # CASCADE bilan truncate (foreign key bog'liqliklariga qaramay)
            quoted = ", ".join(f'"{t}"' for t in to_truncate)
            sql = f'TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE;'
            cursor.execute(sql)

        self.stdout.write(
            self.style.SUCCESS(
                f"Ma'lumotlar o'chirildi ({len(to_truncate)} ta jadval). "
                "django_migrations saqlanib qoldi."
            )
        )
