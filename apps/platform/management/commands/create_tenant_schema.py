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
                    organization_id INTEGER,
                    name VARCHAR(200) NOT NULL,
                    description_uz TEXT,
                    description_ru TEXT,
                    description_en TEXT,
                    address TEXT,
                    city VARCHAR(100),
                    country VARCHAR(3) DEFAULT 'UZ',
                    latitude NUMERIC(17,14),
                    longitude NUMERIC(17,14),
                    star_rating INTEGER,
                    amenities TEXT[] DEFAULT '{}',
                    check_in_time TIME,
                    check_out_time TIME,
                    cancellation_policy VARCHAR(50),
                    quiet_hours BOOLEAN DEFAULT TRUE,
                    alcohol_allowed BOOLEAN DEFAULT TRUE,
                    pets_allowed BOOLEAN DEFAULT FALSE,
                    currency VARCHAR(3) DEFAULT 'USD',
                    timezone VARCHAR(50) DEFAULT 'Asia/Tashkent',
                    photos TEXT[] DEFAULT '{}',
                    is_active BOOLEAN DEFAULT TRUE,
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
                CREATE TABLE IF NOT EXISTS pms_room_type (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    base_rate NUMERIC(10,2),
                    currency VARCHAR(3) DEFAULT 'USD',
                    capacity INTEGER DEFAULT 2,
                    amenities TEXT[] DEFAULT '{}',
                    photos TEXT[] DEFAULT '{}',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_room_type")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_room (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL,
                    room_number VARCHAR(20) NOT NULL,
                    floor INTEGER DEFAULT 1,
                    area NUMERIC(8,2),
                    beds JSONB DEFAULT '[]',
                    amenities TEXT[] DEFAULT '{}',
                    photos TEXT[] DEFAULT '{}',
                    condition VARCHAR(20) DEFAULT 'clean',
                    availability VARCHAR(20) DEFAULT 'available',
                    capacity INTEGER DEFAULT 2,
                    meal_plan VARCHAR(3) DEFAULT 'BB',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(property_id, room_number)
                );
            """)
            self.stdout.write("  Created pms_room")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pms_room_image (
                    id BIGSERIAL PRIMARY KEY,
                    room_id BIGINT NOT NULL REFERENCES pms_room(id) ON DELETE CASCADE,
                    image_url VARCHAR(500) NOT NULL,
                    "order" INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_room_image")

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
                    notes TEXT,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_booking")

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
                CREATE TABLE IF NOT EXISTS pms_rate (
                    id BIGSERIAL PRIMARY KEY,
                    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                    room_type_id BIGINT NOT NULL REFERENCES pms_room_type(id) ON DELETE CASCADE,
                    date_from DATE NOT NULL,
                    date_to DATE NOT NULL,
                    rate NUMERIC(10,2) NOT NULL,
                    currency VARCHAR(3) DEFAULT 'USD',
                    min_stay INTEGER DEFAULT 1,
                    is_weekend_rate BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created pms_rate")

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

            cursor.execute("SET search_path TO public;")

        self.stdout.write(self.style.SUCCESS(f"Tenant schema '{schema_name}' created successfully."))
