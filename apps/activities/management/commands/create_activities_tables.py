from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Create Adventure Activities tables in the public schema"

    def handle(self, *args, **options):
        self.stdout.write("Creating Adventure Activities tables in public schema...")

        with connection.cursor() as cursor:
            # Needed for the EXCLUDE ... USING gist constraint on activity_booking below.
            cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity (
                    id BIGSERIAL PRIMARY KEY,
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),
                    partner_user_id BIGINT NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    category VARCHAR(20) NOT NULL DEFAULT 'extreme',
                    address VARCHAR(500),
                    latitude NUMERIC(9,6),
                    longitude NUMERIC(9,6),
                    buffer_minutes INTEGER NOT NULL DEFAULT 10,
                    requires_prepayment BOOLEAN NOT NULL DEFAULT TRUE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created activity")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activity_partner
                    ON activity (partner_user_id);
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_image (
                    id BIGSERIAL PRIMARY KEY,
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),
                    activity_id BIGINT NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
                    image_url VARCHAR(500) NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created activity_image")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_working_hours (
                    id BIGSERIAL PRIMARY KEY,
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),
                    activity_id BIGINT NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
                    weekday SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                    is_day_off BOOLEAN NOT NULL DEFAULT FALSE,
                    opens_at TIME,
                    closes_at TIME,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT unique_activity_weekday UNIQUE (activity_id, weekday)
                );
            """)
            self.stdout.write("  Created activity_working_hours")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tariff (
                    id BIGSERIAL PRIMARY KEY,
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),
                    activity_id BIGINT NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
                    price NUMERIC(12,2) NOT NULL,
                    min_age INTEGER,
                    max_age INTEGER,
                    min_weight_kg INTEGER,
                    max_weight_kg INTEGER,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created tariff")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tariff_activity
                    ON tariff (activity_id);
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resource (
                    id BIGSERIAL PRIMARY KEY,
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),
                    activity_id BIGINT NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
                    label VARCHAR(255) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created resource")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_resource_activity
                    ON resource (activity_id);
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_booking (
                    id BIGSERIAL PRIMARY KEY,
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),
                    activity_id BIGINT NOT NULL REFERENCES activity(id),
                    tariff_id BIGINT NOT NULL REFERENCES tariff(id),
                    resource_id BIGINT NOT NULL REFERENCES resource(id),
                    client_user_id BIGINT,
                    guest_phone VARCHAR(20),
                    starts_at TIMESTAMPTZ NOT NULL,
                    ends_at TIMESTAMPTZ NOT NULL,
                    buffer_minutes_snapshot INTEGER NOT NULL DEFAULT 0,
                    blocked_until TIMESTAMPTZ NOT NULL,
                    price_snapshot NUMERIC(12,2) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending_payment',
                    cancellation_reason VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    -- Core correctness guarantee: within the same resource, two
                    -- "in-flight" bookings (pending_payment/confirmed) can never
                    -- reserve overlapping time. This is enforced by Postgres itself,
                    -- so it holds even under concurrent inserts (no app-level locking
                    -- required for correctness — the Redis hold in hold_service.py is
                    -- only a soft UX lock, not the source of truth).
                    CONSTRAINT no_overlapping_resource_booking
                        EXCLUDE USING gist (
                            resource_id WITH =,
                            tstzrange(starts_at, blocked_until, '[)') WITH &&
                        )
                        WHERE (status IN ('pending_payment', 'confirmed'))
                );
            """)
            self.stdout.write("  Created activity_booking")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activity_booking_resource_time
                    ON activity_booking (resource_id, starts_at);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activity_booking_client
                    ON activity_booking (client_user_id);
            """)

        self.stdout.write(self.style.SUCCESS("Adventure Activities tables created successfully."))
