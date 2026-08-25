from django.core.management.base import BaseCommand
from django.db import connection

# Every reference list Hotelios exposes has the same shape: an integer id and a
# `names` array of {locale, value}. They differ only in whether they carry a
# `filter_flag`, so the DDL is generated rather than written out eleven times.
REFERENCE_TABLES = (
    ("hotelios_country", False),
    ("hotelios_hotel_type", False),
    ("hotelios_facility", True),
    ("hotelios_equipment", True),
    ("hotelios_nearby_place_type", False),
    ("hotelios_service_in_room", False),
    ("hotelios_bed_type", False),
)


class Command(BaseCommand):
    help = "Create the Hotelios inventory and booking tables in the public schema"

    def handle(self, *args, **options):
        self.stdout.write("Creating Hotelios tables in public schema...")

        with connection.cursor() as cursor:
            self._reference_tables(cursor)
            self._geography(cursor)
            self._inventory(cursor)
            self._bookings(cursor)

        self.stdout.write(self.style.SUCCESS("Hotelios tables created successfully."))

    # -- reference --------------------------------------------------------

    def _reference_tables(self, cursor) -> None:
        for table, has_filter_flag in REFERENCE_TABLES:
            filter_column = (
                "filter_flag BOOLEAN NOT NULL DEFAULT FALSE," if has_filter_flag else ""
            )
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id INTEGER PRIMARY KEY,
                    -- {{"uz": "...", "ru": "...", "en": "..."}}
                    names JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    {filter_column}
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write(f"  Created {table}")

        # Stars and currencies break the pattern: a star has a plain name, a
        # currency is keyed by its code.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_star (
                id INTEGER PRIMARY KEY,
                name VARCHAR(32) NOT NULL,
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        self.stdout.write("  Created hotelios_star")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_currency (
                code VARCHAR(8) PRIMARY KEY,
                name VARCHAR(64) NOT NULL,
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        self.stdout.write("  Created hotelios_currency")

    # -- geography --------------------------------------------------------

    def _geography(self, cursor) -> None:
        # `name_en` sits beside `names` on the rows people search by name. The
        # JSONB holds every locale; the plain column is what an index can be
        # built on, and city autocomplete is the first thing a booking flow
        # asks for.
        #
        # `country_id` and `region_id` are plain columns, not foreign keys.
        # This is a mirror of somebody else's catalogue: the phases land in
        # separate calls and a city can arrive before the region it names.
        # Refusing to store it would lose a real city over an ordering detail.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_region (
                id INTEGER PRIMARY KEY,
                country_id INTEGER,
                names JSONB NOT NULL DEFAULT '{}'::jsonb,
                name_en VARCHAR(255),
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_region_country
                ON hotelios_region (country_id);
        """)
        self.stdout.write("  Created hotelios_region")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_city (
                id INTEGER PRIMARY KEY,
                region_id INTEGER,
                names JSONB NOT NULL DEFAULT '{}'::jsonb,
                name_en VARCHAR(255),
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_city_region
                ON hotelios_city (region_id);
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_city_name
                ON hotelios_city (LOWER(name_en));
        """)
        self.stdout.write("  Created hotelios_city")

    # -- inventory --------------------------------------------------------

    def _inventory(self, cursor) -> None:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_hotel (
                id BIGINT PRIMARY KEY,
                hotel_type_id INTEGER,
                city_id INTEGER,
                star_id INTEGER,
                currency VARCHAR(8),

                latitude NUMERIC(10,7),
                longitude NUMERIC(10,7),
                postal_code VARCHAR(32),

                names JSONB NOT NULL DEFAULT '{}'::jsonb,
                name_en VARCHAR(500),
                address JSONB NOT NULL DEFAULT '{}'::jsonb,
                description JSONB NOT NULL DEFAULT '{}'::jsonb,

                -- Kept as sent: nested arrays with their own per-entry rules
                -- (group vs individual check-in windows, early/late penalty
                -- bands, free-child ages). Splitting them into columns would
                -- lose structure we only ever hand back to the apps whole.
                check_in JSONB NOT NULL DEFAULT '[]'::jsonb,
                check_out JSONB NOT NULL DEFAULT '[]'::jsonb,
                guest_age_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                facilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                photos JSONB NOT NULL DEFAULT '[]'::jsonb,
                nearby_places JSONB NOT NULL DEFAULT '[]'::jsonb,
                services_in_room JSONB NOT NULL DEFAULT '[]'::jsonb,

                provider_updated_at TIMESTAMPTZ,
                raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                -- Set on every sync pass that saw this hotel. A row whose
                -- synced_at falls behind the run that touched its siblings has
                -- disappeared from the supplier's catalogue.
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_hotel_city
                ON hotelios_hotel (city_id) WHERE is_active;
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_hotel_stars
                ON hotelios_hotel (star_id) WHERE is_active;
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_hotel_name
                ON hotelios_hotel (LOWER(name_en));
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_hotel_facilities
                ON hotelios_hotel USING gin (facilities jsonb_path_ops);
        """)
        self.stdout.write("  Created hotelios_hotel")

        # The key is (hotel_id, room_type_id), not room_type_id alone. Hotelios
        # documents the id as globally unique, and its data is not: id
        # 20000281 comes back under six different hotels. Keying on the id by
        # itself silently collapses those into one row and loses the room types
        # of every hotel but the last one written.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_room_type (
                hotel_id BIGINT NOT NULL
                    REFERENCES hotelios_hotel(id) ON DELETE CASCADE,
                room_type_id BIGINT NOT NULL,
                holding_capacity INTEGER,
                bed_type INTEGER,
                extra_bed BOOLEAN NOT NULL DEFAULT FALSE,
                area NUMERIC(8,2),

                names JSONB NOT NULL DEFAULT '{}'::jsonb,
                name_en VARCHAR(500),
                description JSONB NOT NULL DEFAULT '{}'::jsonb,
                photos JSONB NOT NULL DEFAULT '[]'::jsonb,
                equipments JSONB NOT NULL DEFAULT '[]'::jsonb,

                raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                PRIMARY KEY (hotel_id, room_type_id)
            );
        """)
        # A Booking-Flow payload names a room by `room_type_id` alone, so the
        # lookup that resolves one back to a hotel needs its own index.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_room_type_id
                ON hotelios_room_type (room_type_id);
        """)
        self.stdout.write("  Created hotelios_room_type")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_sync_run (
                id BIGSERIAL PRIMARY KEY,
                -- 'references' | 'geography' | 'hotels' | 'room_types' | 'full'
                scope VARCHAR(32) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'running',
                pages_done INTEGER NOT NULL DEFAULT 0,
                pages_total INTEGER,
                records INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_sync_run_scope
                ON hotelios_sync_run (scope, started_at DESC);
        """)
        self.stdout.write("  Created hotelios_sync_run")

    # -- bookings ---------------------------------------------------------

    def _bookings(self, cursor) -> None:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_booking (
                id BIGSERIAL PRIMARY KEY,
                guid UUID NOT NULL DEFAULT gen_random_uuid(),

                -- What we send Hotelios as `external_id`. It must be unique on
                -- their side, and it is how a booking is recovered when the
                -- Create call times out after they accepted it.
                external_id VARCHAR(64) NOT NULL UNIQUE,
                provider_booking_id VARCHAR(64) UNIQUE,
                quote_id VARCHAR(64),

                hotel_id BIGINT,
                status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',

                check_in TIMESTAMPTZ,
                check_out TIMESTAMPTZ,
                is_resident BOOLEAN NOT NULL DEFAULT FALSE,
                nationality VARCHAR(2),
                residence VARCHAR(2),

                price NUMERIC(14,2),
                currency VARCHAR(8),
                comment TEXT,
                hotel_confirmation_number VARCHAR(120),
                additional_information JSONB,

                client_user_id BIGINT,
                b2b_company_id BIGINT,
                b2b_user_id BIGINT,
                b2b_trip_id BIGINT,

                provider_created_at TIMESTAMPTZ,
                raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_booking_client
                ON hotelios_booking (client_user_id) WHERE client_user_id IS NOT NULL;
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_booking_company
                ON hotelios_booking (b2b_company_id, created_at DESC)
                WHERE b2b_company_id IS NOT NULL;
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_booking_trip
                ON hotelios_booking (b2b_trip_id) WHERE b2b_trip_id IS NOT NULL;
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_booking_status
                ON hotelios_booking (status, updated_at);
        """)
        self.stdout.write("  Created hotelios_booking")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_booking_room (
                id BIGSERIAL PRIMARY KEY,
                booking_id BIGINT NOT NULL
                    REFERENCES hotelios_booking(id) ON DELETE CASCADE,

                option_ref_id TEXT,
                room_type_id BIGINT,
                room_type_name VARCHAR(500),
                rate_plan_id BIGINT,

                meal_plan VARCHAR(4),
                included_meal_options JSONB NOT NULL DEFAULT '[]'::jsonb,
                extra_bed_added BOOLEAN NOT NULL DEFAULT FALSE,

                -- Refundable or not, the free-cancellation deadline, and the
                -- penalties. This is what a guest is shown before they pay and
                -- what decides whether a cancellation costs anything, so it is
                -- stored as the provider stated it at booking time.
                cancellation_policy JSONB,
                price NUMERIC(14,2),
                price_breakdown JSONB,
                guests JSONB NOT NULL DEFAULT '[]'::jsonb,

                b2b_employee_id BIGINT,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_booking_room_booking
                ON hotelios_booking_room (booking_id);
        """)
        self.stdout.write("  Created hotelios_booking_room")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotelios_booking_event (
                id BIGSERIAL PRIMARY KEY,
                booking_id BIGINT NOT NULL
                    REFERENCES hotelios_booking(id) ON DELETE CASCADE,
                previous_status VARCHAR(16),
                status VARCHAR(16) NOT NULL,
                source VARCHAR(16) NOT NULL DEFAULT 'api',
                payload JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hotelios_booking_event_booking
                ON hotelios_booking_event (booking_id, created_at DESC);
        """)
        self.stdout.write("  Created hotelios_booking_event")
