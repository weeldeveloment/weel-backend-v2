from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection


BOOKING_COLUMNS_SQL = """
ALTER TABLE {schema}.pms_booking
    ADD COLUMN IF NOT EXISTS external_provider VARCHAR(50),
    ADD COLUMN IF NOT EXISTS external_reservation_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS external_room_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS external_payload_ref JSONB DEFAULT '{{}}',
    ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS {schema}_pms_booking_external_ref_uidx
    ON {schema}.pms_booking (property_id, external_provider, external_reservation_id)
    WHERE external_provider IS NOT NULL AND external_reservation_id IS NOT NULL;
"""

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.pms_bookingcom_connection (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES {schema}.pms_property(id) ON DELETE CASCADE UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    bookingcom_property_id VARCHAR(255) NOT NULL,
    api_url VARCHAR(500) NOT NULL,
    api_token TEXT,
    username VARCHAR(255),
    password TEXT,
    last_successful_sync_at TIMESTAMPTZ,
    last_synced_at TIMESTAMPTZ,
    last_sync_status VARCHAR(30),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema}.pms_bookingcom_room_mapping (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES {schema}.pms_property(id) ON DELETE CASCADE,
    external_room_id VARCHAR(255) NOT NULL,
    room_id BIGINT REFERENCES {schema}.pms_room(id) ON DELETE SET NULL,
    room_type_id BIGINT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(property_id, external_room_id)
);

CREATE TABLE IF NOT EXISTS {schema}.pms_bookingcom_sync_run (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES {schema}.pms_property(id) ON DELETE CASCADE,
    connection_id BIGINT REFERENCES {schema}.pms_bookingcom_connection(id) ON DELETE SET NULL,
    triggered_by VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL,
    stats JSONB DEFAULT '{{}}',
    error_message TEXT,
    sync_cursor_from TIMESTAMPTZ,
    sync_cursor_to TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema}.pms_bookingcom_sync_error (
    id BIGSERIAL PRIMARY KEY,
    sync_run_id BIGINT REFERENCES {schema}.pms_bookingcom_sync_run(id) ON DELETE CASCADE,
    property_id BIGINT NOT NULL REFERENCES {schema}.pms_property(id) ON DELETE CASCADE,
    external_reservation_id VARCHAR(255),
    external_room_id VARCHAR(255),
    code VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    payload JSONB DEFAULT '{{}}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class Command(BaseCommand):
    help = "Ensure Booking.com integration tables and booking metadata columns exist in all tenant schemas."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name LIKE 'tenant\\_%' ESCAPE '\\'
                ORDER BY schema_name
                """
            )
            schemas = [row[0] for row in cursor.fetchall()]

            for schema in schemas:
                cursor.execute(BOOKING_COLUMNS_SQL.format(schema=schema))
                cursor.execute(TABLES_SQL.format(schema=schema))
                self.stdout.write(f"Updated schema: {schema}")

        self.stdout.write(self.style.SUCCESS(f"Booking.com schema synced for {len(schemas)} tenant schema(s)."))
