from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Create the Bookhara avia booking tables in the public schema"

    def handle(self, *args, **options):
        self.stdout.write("Creating avia tables in public schema...")

        with connection.cursor() as cursor:
            # Bookhara owns the booking; this table is our record of it. The
            # authoritative state always comes from GET /api/v1/booking/{id},
            # so `raw` keeps the last full payload we saw and the columns
            # beside it exist to be queried, filtered and joined against.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS avia_booking (
                    id BIGSERIAL PRIMARY KEY,
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),

                    provider_booking_id VARCHAR(64) NOT NULL UNIQUE,
                    booking_number VARCHAR(64),
                    -- Bookhara's offer identifiers are opaque and long — the
                    -- ones staging issues run to about 700 characters — so
                    -- this cannot be a bounded VARCHAR.
                    offer_id TEXT,

                    status VARCHAR(32) NOT NULL,
                    offer_type VARCHAR(16),
                    flight_type VARCHAR(20),
                    fare_family_type VARCHAR(120),
                    is_charter BOOLEAN NOT NULL DEFAULT FALSE,
                    refund_availability BOOLEAN NOT NULL DEFAULT FALSE,

                    amount NUMERIC(14,2),
                    prev_amount NUMERIC(14,2),
                    currency VARCHAR(3),

                    payer_name VARCHAR(255),
                    payer_email VARCHAR(255),
                    payer_tel VARCHAR(32),

                    -- Who, on our side, this booking belongs to. A consumer
                    -- booking fills client_user_id; a corporate one fills the
                    -- b2b_* columns. No FKs to b2b_* so a company deletion
                    -- cannot orphan a real airline ticket.
                    client_user_id BIGINT,
                    b2b_company_id BIGINT,
                    b2b_user_id BIGINT,
                    b2b_trip_id BIGINT,
                    b2b_employee_id BIGINT,

                    provider_created_at TIMESTAMPTZ,
                    -- Bookhara's `expire`: auto-cancellation deadline for an
                    -- unpaid booking. Past it, the id 404s.
                    expires_at TIMESTAMPTZ,

                    directions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    information_for_clients JSONB NOT NULL DEFAULT '[]'::jsonb,
                    additional_services JSONB,
                    fiscalization JSONB,
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created avia_booking")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_avia_booking_client
                    ON avia_booking (client_user_id) WHERE client_user_id IS NOT NULL;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_avia_booking_company
                    ON avia_booking (b2b_company_id, created_at DESC)
                    WHERE b2b_company_id IS NOT NULL;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_avia_booking_trip
                    ON avia_booking (b2b_trip_id) WHERE b2b_trip_id IS NOT NULL;
            """)
            # The polling task asks for exactly this: orders still waiting on
            # ticket numbers, oldest first.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_avia_booking_status
                    ON avia_booking (status, updated_at);
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS avia_booking_passenger (
                    id BIGSERIAL PRIMARY KEY,
                    booking_id BIGINT NOT NULL
                        REFERENCES avia_booking(id) ON DELETE CASCADE,

                    -- Bookhara's passenger `key`, e.g. PETROVA_ALLA_AS76123646_06-01-1983.
                    passenger_key VARCHAR(255),
                    first_name VARCHAR(120) NOT NULL,
                    last_name VARCHAR(120) NOT NULL,
                    middle_name VARCHAR(120),
                    age_group VARCHAR(3) NOT NULL,
                    gender VARCHAR(1),
                    birthdate DATE,
                    citizenship VARCHAR(2),
                    email VARCHAR(255),
                    tel VARCHAR(32),

                    doc_type VARCHAR(8),
                    doc_number VARCHAR(64),
                    doc_expire DATE,

                    price NUMERIC(14,2),
                    -- PNRs, airline locators and ticket numbers. Ticket
                    -- numbers are null until the order reaches `ticketed`.
                    tickets JSONB NOT NULL DEFAULT '[]'::jsonb,
                    itinerary_receipt_url VARCHAR(500),

                    b2b_employee_id BIGINT,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    -- Re-reading a booking must update the passenger rows in
                    -- place rather than duplicating them.
                    CONSTRAINT unique_avia_passenger_per_booking
                        UNIQUE (booking_id, passenger_key)
                );
            """)
            self.stdout.write("  Created avia_booking_passenger")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_avia_passenger_booking
                    ON avia_booking_passenger (booking_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_avia_passenger_employee
                    ON avia_booking_passenger (b2b_employee_id)
                    WHERE b2b_employee_id IS NOT NULL;
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS avia_booking_event (
                    id BIGSERIAL PRIMARY KEY,
                    booking_id BIGINT NOT NULL
                        REFERENCES avia_booking(id) ON DELETE CASCADE,
                    previous_status VARCHAR(32),
                    status VARCHAR(32) NOT NULL,
                    -- 'callback' | 'poll' | 'api' — how we learned about it.
                    source VARCHAR(16) NOT NULL DEFAULT 'api',
                    payload JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created avia_booking_event")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_avia_event_booking
                    ON avia_booking_event (booking_id, created_at DESC);
            """)

        self.stdout.write(self.style.SUCCESS("Avia tables created successfully."))
