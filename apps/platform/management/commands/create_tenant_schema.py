from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Create a tenant schema with PMS tables"

    def add_arguments(self, parser):
        parser.add_argument("schema_name", type=str, help="Name of the tenant schema")

    def handle(self, *args, **options):
        schema_name = options["schema_name"]
        self.stdout.write(f"Creating tenant schema: {schema_name}...")

        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name};")
            self.stdout.write(f"  Created schema: {schema_name}")

            cursor.execute(f"SET search_path TO {schema_name}, public;")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_property (
                    id BIGSERIAL PRIMARY KEY,
                    organization_id INTEGER NOT NULL,
                    partner_user_id INTEGER,
                    name VARCHAR(200) NOT NULL,
                    description_uz TEXT,
                    description_ru TEXT,
                    description_en TEXT,
                    address TEXT,
                    full_address TEXT,
                    city VARCHAR(100),
                    country VARCHAR(3) DEFAULT 'UZ',
                    latitude NUMERIC(17,14),
                    longitude NUMERIC(17,14),
                    star_rating INTEGER,
                    weel_classification VARCHAR(20),
                    themes TEXT[] DEFAULT '{}',
                    amenities TEXT[] DEFAULT '{}',
                    legal_info JSONB DEFAULT '{}',
                    check_in_time TIME,
                    check_out_time TIME,
                    cancellation_policy VARCHAR(50),
                    quiet_hours BOOLEAN DEFAULT TRUE,
                    alcohol_allowed BOOLEAN DEFAULT TRUE,
                    pets_allowed BOOLEAN DEFAULT FALSE,
                    timezone VARCHAR(50) DEFAULT 'Asia/Tashkent',
                    photos TEXT[] DEFAULT '{}',
                    is_active BOOLEAN DEFAULT TRUE,
                    is_testing BOOLEAN NOT NULL DEFAULT FALSE,
                    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
                    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                    is_recommended BOOLEAN NOT NULL DEFAULT FALSE,
                    verification_status VARCHAR(20) DEFAULT 'waiting',
                    guid UUID NOT NULL DEFAULT gen_random_uuid(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_property")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_property_image (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    image_url VARCHAR(500) NOT NULL,
                    "order" INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_property_image")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_room (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    room_type_name VARCHAR(100),
                    room_type_preset VARCHAR(20),
                    room_number VARCHAR(20) NOT NULL,
                    display_name VARCHAR(200),
                    floor INTEGER DEFAULT 1,
                    area NUMERIC(8,2),
                    bedroom_count INTEGER DEFAULT 1,
                    beds JSONB DEFAULT '[]',
                    amenities TEXT[] DEFAULT '{}',
                    photos TEXT[] DEFAULT '{}',
                    condition VARCHAR(20) DEFAULT 'clean',
                    availability VARCHAR(20) DEFAULT 'available',
                    capacity INTEGER DEFAULT 2,
                    meal_plan VARCHAR(3) DEFAULT 'BB',
                    base_price NUMERIC(10,2),
                    currency VARCHAR(3) NOT NULL DEFAULT 'UZS',
                    cover_photo_index INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(property_id, room_number)
                );
            """)
            self.stdout.write("  Created pms_room")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_calendar_slot (
                    id BIGSERIAL PRIMARY KEY,
                    room_id BIGINT NOT NULL REFERENCES pms_room(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'available',
                    hold_expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(room_id, date)
                );
            """)
            self.stdout.write("  Created pms_calendar_slot")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_guest (
                    id BIGSERIAL PRIMARY KEY,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100),
                    email VARCHAR(254),
                    phone VARCHAR(32),
                    id_document JSONB DEFAULT '{}',
                    preferences JSONB DEFAULT '{}',
                    is_vip BOOLEAN DEFAULT FALSE,
                    is_blacklisted BOOLEAN DEFAULT FALSE,
                    notes TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_guest")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_booking (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    room_id BIGINT NOT NULL REFERENCES pms_room(id) ON DELETE RESTRICT,
                    guest_id BIGINT REFERENCES pms_guest(id) ON DELETE SET NULL,
                    booking_number VARCHAR(20) NOT NULL UNIQUE,
                    check_in DATE NOT NULL,
                    check_out DATE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'new',
                    source VARCHAR(20) NOT NULL DEFAULT 'direct',
                    meal_plan VARCHAR(3) NOT NULL DEFAULT 'RO',
                    adult_count INTEGER DEFAULT 1,
                    child_count INTEGER DEFAULT 0,
                    rate NUMERIC(10,2),
                    currency VARCHAR(3) DEFAULT 'USD',
                    payment_status VARCHAR(20) DEFAULT 'pending',
                    total_cost NUMERIC(10,2),
                    hold_amount NUMERIC(10,2),
                    confirmed_at TIMESTAMPTZ,
                    confirmation_deadline TIMESTAMPTZ,
                    b2b_company_id BIGINT,
                    voucher_number VARCHAR(50),
                    notes TEXT,
                    created_by BIGINT,
                    external_provider VARCHAR(50),
                    external_reservation_id VARCHAR(255),
                    external_room_id VARCHAR(255),
                    external_payload_ref JSONB DEFAULT '{}',
                    imported_at TIMESTAMPTZ,
                    last_synced_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_booking")
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS pms_booking_external_ref_uidx
                ON pms_booking (property_id, external_provider, external_reservation_id)
                WHERE external_provider IS NOT NULL AND external_reservation_id IS NOT NULL;
            """)
            self.stdout.write("  Created pms_booking external reference index")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_booking_history (
                    id BIGSERIAL PRIMARY KEY,
                    booking_id BIGINT NOT NULL REFERENCES pms_booking(id) ON DELETE CASCADE,
                    action VARCHAR(50) NOT NULL,
                    previous_value JSONB DEFAULT '{}',
                    new_value JSONB DEFAULT '{}',
                    user_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_booking_history")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_review (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    booking_id BIGINT REFERENCES pms_booking(id) ON DELETE SET NULL,
                    guest_name VARCHAR(200) NOT NULL,
                    rating NUMERIC(2,1) NOT NULL,
                    categories JSONB DEFAULT '{}',
                    text TEXT,
                    hotel_response TEXT,
                    response_date TIMESTAMPTZ,
                    is_complained BOOLEAN DEFAULT FALSE,
                    complaint_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_review")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_bookingcom_connection (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE UNIQUE,
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
            """)
            self.stdout.write("  Created pms_bookingcom_connection")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_bookingcom_room_mapping (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    external_room_id VARCHAR(255) NOT NULL,
                    room_id BIGINT REFERENCES pms_room(id) ON DELETE SET NULL,
                    room_type_id BIGINT,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(property_id, external_room_id)
                );
            """)
            self.stdout.write("  Created pms_bookingcom_room_mapping")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_bookingcom_sync_run (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    connection_id BIGINT REFERENCES pms_bookingcom_connection(id) ON DELETE SET NULL,
                    triggered_by VARCHAR(50) NOT NULL,
                    status VARCHAR(30) NOT NULL,
                    stats JSONB DEFAULT '{}',
                    error_message TEXT,
                    sync_cursor_from TIMESTAMPTZ,
                    sync_cursor_to TIMESTAMPTZ,
                    started_at TIMESTAMPTZ,
                    finished_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_bookingcom_sync_run")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_bookingcom_sync_error (
                    id BIGSERIAL PRIMARY KEY,
                    sync_run_id BIGINT REFERENCES pms_bookingcom_sync_run(id) ON DELETE CASCADE,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    external_reservation_id VARCHAR(255),
                    external_room_id VARCHAR(255),
                    code VARCHAR(100) NOT NULL,
                    message TEXT NOT NULL,
                    payload JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_bookingcom_sync_error")

            cursor.execute("SET search_path TO public;")

        self.stdout.write(self.style.SUCCESS(f"Tenant schema '{schema_name}' created successfully."))
