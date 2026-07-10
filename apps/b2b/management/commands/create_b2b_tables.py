from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Create B2B tables in the public schema"

    def handle(self, *args, **options):
        self.stdout.write("Creating B2B tables in public schema...")

        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_company (
                    id BIGSERIAL PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    legal_name VARCHAR(300),
                    inn VARCHAR(20),
                    city VARCHAR(100),
                    district VARCHAR(100),
                    legal_address TEXT,
                    industry VARCHAR(100),
                    employee_count INTEGER,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_company")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_user (
                    id BIGSERIAL PRIMARY KEY,
                    company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                    phone VARCHAR(20) NOT NULL UNIQUE,
                    email VARCHAR(254),
                    first_name VARCHAR(100),
                    last_name VARCHAR(100),
                    role VARCHAR(20) NOT NULL DEFAULT 'performer',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_user")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_user_session (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES b2b_user(id) ON DELETE CASCADE,
                    token VARCHAR(500) NOT NULL UNIQUE,
                    expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_user_session")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_department (
                    id BIGSERIAL PRIMARY KEY,
                    company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_department")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_employee (
                    id BIGSERIAL PRIMARY KEY,
                    company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                    department_id BIGINT REFERENCES b2b_department(id) ON DELETE SET NULL,
                    full_name VARCHAR(200) NOT NULL,
                    position VARCHAR(100),
                    email VARCHAR(254),
                    phone VARCHAR(20),
                    date_of_birth DATE,
                    passport_series VARCHAR(10),
                    passport_number VARCHAR(20),
                    passport_upload VARCHAR(500),
                    pinfl VARCHAR(20),
                    individual_limit NUMERIC(12,2),
                    status VARCHAR(20) DEFAULT 'available',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS passport_upload VARCHAR(500);
            """)
            self.stdout.write("  Created b2b_employee")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_business_trip (
                    id BIGSERIAL PRIMARY KEY,
                    company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                    name VARCHAR(200) NOT NULL,
                    destination_city VARCHAR(100),
                    start_date DATE,
                    end_date DATE,
                    budget NUMERIC(14,2),
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    created_by BIGINT REFERENCES b2b_user(id) ON DELETE SET NULL,
                    notes TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_business_trip")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_trip_employee (
                    id BIGSERIAL PRIMARY KEY,
                    trip_id BIGINT NOT NULL REFERENCES b2b_business_trip(id) ON DELETE CASCADE,
                    employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                    property_id BIGINT,
                    room_id BIGINT,
                    check_in DATE,
                    check_out DATE,
                    pms_booking_id BIGINT,
                    status VARCHAR(20) NOT NULL DEFAULT 'invited',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_trip_employee")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_travel_policy (
                    id BIGSERIAL PRIMARY KEY,
                    company_id BIGINT NOT NULL UNIQUE REFERENCES b2b_company(id) ON DELETE CASCADE,
                    budget_per_trip NUMERIC(14,2),
                    monthly_budget NUMERIC(14,2),
                    allowed_star_ratings TEXT[] DEFAULT '{}',
                    allowed_weel_classifications TEXT[] DEFAULT '{}',
                    blacklisted_properties TEXT[] DEFAULT '{}',
                    preferred_properties TEXT[] DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_travel_policy")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_travel_policy_rule (
                    id BIGSERIAL PRIMARY KEY,
                    policy_id BIGINT NOT NULL REFERENCES b2b_travel_policy(id) ON DELETE CASCADE,
                    applies_to VARCHAR(20) NOT NULL DEFAULT 'all',
                    target_id BIGINT,
                    budget_limit NUMERIC(14,2),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_travel_policy_rule")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_budget_request (
                    id BIGSERIAL PRIMARY KEY,
                    trip_id BIGINT REFERENCES b2b_business_trip(id) ON DELETE CASCADE,
                    employee_id BIGINT REFERENCES b2b_employee(id) ON DELETE CASCADE,
                    department_id BIGINT REFERENCES b2b_department(id) ON DELETE CASCADE,
                    requested_by BIGINT REFERENCES b2b_user(id) ON DELETE SET NULL,
                    amount NUMERIC(14,2) NOT NULL,
                    description TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    reviewed_by BIGINT REFERENCES b2b_user(id) ON DELETE SET NULL,
                    reviewed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("ALTER TABLE b2b_budget_request ALTER COLUMN trip_id DROP NOT NULL;")
            cursor.execute("ALTER TABLE b2b_budget_request ALTER COLUMN employee_id DROP NOT NULL;")
            cursor.execute("ALTER TABLE b2b_budget_request ADD COLUMN IF NOT EXISTS department_id BIGINT REFERENCES b2b_department(id) ON DELETE CASCADE;")
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'b2b_budget_request' AND column_name = 'reason'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'b2b_budget_request' AND column_name = 'description'
                    ) THEN
                        ALTER TABLE b2b_budget_request RENAME COLUMN reason TO description;
                    END IF;
                END $$;
            """)
            self.stdout.write("  Created b2b_budget_request")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_travel_voucher (
                    id BIGSERIAL PRIMARY KEY,
                    trip_id BIGINT NOT NULL UNIQUE REFERENCES b2b_business_trip(id) ON DELETE CASCADE,
                    voucher_number VARCHAR(50) NOT NULL UNIQUE,
                    pdf_url VARCHAR(500),
                    generated_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_travel_voucher")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_hotel_booking_request (
                    id BIGSERIAL PRIMARY KEY,
                    company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                    trip_id BIGINT REFERENCES b2b_business_trip(id) ON DELETE SET NULL,
                    hotel_guid VARCHAR(300) NOT NULL,
                    tenant_schema VARCHAR(100) NOT NULL,
                    hotel_property_id BIGINT NOT NULL,
                    hotel_name VARCHAR(200),
                    check_in DATE NOT NULL,
                    check_out DATE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    requested_by BIGINT REFERENCES b2b_user(id) ON DELETE SET NULL,
                    reviewed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_hotel_booking_request")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_hotel_booking_room (
                    id BIGSERIAL PRIMARY KEY,
                    booking_request_id BIGINT NOT NULL REFERENCES b2b_hotel_booking_request(id) ON DELETE CASCADE,
                    room_id BIGINT NOT NULL,
                    room_name VARCHAR(200),
                    pms_booking_id BIGINT,
                    price_per_night NUMERIC(10,2),
                    total_price NUMERIC(12,2),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_hotel_booking_room")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_hotel_booking_room_employee (
                    id BIGSERIAL PRIMARY KEY,
                    booking_room_id BIGINT NOT NULL REFERENCES b2b_hotel_booking_room(id) ON DELETE CASCADE,
                    employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_hotel_booking_room_employee")

        self.stdout.write(self.style.SUCCESS("B2B tables created successfully."))
