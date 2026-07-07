from django.core.management.base import BaseCommand
from django.db import connection
from django.db.utils import IntegrityError


class Command(BaseCommand):
    help = "Add new columns to existing tenant PMS tables (migration for existing schemas)"

    def add_arguments(self, parser):
        parser.add_argument("schema_name", type=str, help="Name of the tenant schema to migrate")

    def handle(self, *args, **options):
        schema_name = options["schema_name"]
        self.stdout.write(f"Migrating tenant schema: {schema_name}...")

        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {schema_name}, public;")

            migrations = [
                # pms_property — new columns
                ("pms_property", "full_address", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS full_address TEXT"),
                ("pms_property", "weel_classification", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS weel_classification VARCHAR(20)"),
                ("pms_property", "themes", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS themes TEXT[] DEFAULT '{}'"),
                ("pms_property", "legal_info", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS legal_info JSONB DEFAULT '{}'"),
                # pms_room_type — new columns
                ("pms_room_type", "preset", "ALTER TABLE pms_room_type ADD COLUMN IF NOT EXISTS preset VARCHAR(20)"),
                ("pms_room_type", "custom_name", "ALTER TABLE pms_room_type ADD COLUMN IF NOT EXISTS custom_name VARCHAR(100)"),
                # pms_room — new columns
                ("pms_room", "display_name", "ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS display_name VARCHAR(200)"),
                ("pms_room", "bedroom_count", "ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS bedroom_count INTEGER DEFAULT 1"),
                # pms_property — partner_user_id
                ("pms_property", "partner_user_id", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS partner_user_id INTEGER"),
                # pms_property — verification fields
                ("pms_property", "is_verified", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE"),
                ("pms_property", "is_archived", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE"),
                ("pms_property", "is_recommended", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS is_recommended BOOLEAN NOT NULL DEFAULT FALSE"),
                ("pms_property", "verification_status", "ALTER TABLE pms_property ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'waiting'"),
                # pms_booking — new columns
                ("pms_booking", "hold_amount", "ALTER TABLE pms_booking ADD COLUMN IF NOT EXISTS hold_amount NUMERIC(10,2)"),
                ("pms_booking", "confirmed_at", "ALTER TABLE pms_booking ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ"),
                ("pms_booking", "confirmation_deadline", "ALTER TABLE pms_booking ADD COLUMN IF NOT EXISTS confirmation_deadline TIMESTAMPTZ"),
                ("pms_booking", "b2b_company_id", "ALTER TABLE pms_booking ADD COLUMN IF NOT EXISTS b2b_company_id BIGINT"),
                ("pms_booking", "voucher_number", "ALTER TABLE pms_booking ADD COLUMN IF NOT EXISTS voucher_number VARCHAR(50)"),
            ]

            for table, col, sql in migrations:
                try:
                    cursor.execute(sql)
                    self.stdout.write(f"  ✓ {table}.{col}")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  ✗ {table}.{col}: {e}"))

            try:
                cursor.execute("SELECT COUNT(*) FROM pms_property WHERE organization_id IS NULL")
                null_count = int(cursor.fetchone()[0] or 0)
                if null_count > 0:
                    raise IntegrityError(
                        f"Cannot enforce NOT NULL on pms_property.organization_id; found {null_count} NULL rows."
                    )
                cursor.execute("ALTER TABLE pms_property ALTER COLUMN organization_id SET NOT NULL")
                self.stdout.write("  ✓ pms_property.organization_id NOT NULL")
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  ✗ pms_property.organization_id NOT NULL: {e}"))

            cursor.execute("SET search_path TO public;")

        self.stdout.write(self.style.SUCCESS(f"Schema '{schema_name}' migrated successfully."))
