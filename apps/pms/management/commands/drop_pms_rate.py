from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Drop pms_rate table from all tenant schemas (rate system removed)."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name LIKE 'tenant_%'
                """
            )
            schemas = [row[0] for row in cursor.fetchall()]

        dropped = 0
        for schema in schemas:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"DROP TABLE IF EXISTS {schema}.pms_rate CASCADE"
                    )
                dropped += 1
            except Exception as e:
                self.stderr.write(f"  Failed dropping {schema}.pms_rate: {e}")

        self.stdout.write(self.style.SUCCESS(f"Dropped pms_rate from {dropped}/{len(schemas)} tenant schemas."))
