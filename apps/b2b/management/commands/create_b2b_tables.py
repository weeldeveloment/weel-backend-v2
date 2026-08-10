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
                    color VARCHAR(20) NOT NULL DEFAULT '#7C3AED',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                ALTER TABLE b2b_department ADD COLUMN IF NOT EXISTS color VARCHAR(20) NOT NULL DEFAULT '#7C3AED';
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
                    passport_pinfl VARCHAR(20),
                    individual_limit NUMERIC(12,2),
                    status VARCHAR(20) DEFAULT 'available',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS passport_upload_front VARCHAR(500);
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS passport_upload_back VARCHAR(500);
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS photo VARCHAR(500);
            """)
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'b2b_employee' AND column_name = 'pinfl'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'b2b_employee' AND column_name = 'passport_pinfl'
                    ) THEN
                        ALTER TABLE b2b_employee RENAME COLUMN pinfl TO passport_pinfl;
                    END IF;
                END $$;
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS passport_pinfl VARCHAR(20);
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee DROP COLUMN IF EXISTS passport_number;
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee DROP COLUMN IF EXISTS pinfl;
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee DROP COLUMN IF EXISTS passport_upload;
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS passport_upload_front VARCHAR(500);
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS passport_upload_back VARCHAR(500);
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'employee';
            """)
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS fcm_token VARCHAR(500);
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
                    review_description TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("ALTER TABLE b2b_budget_request ALTER COLUMN trip_id DROP NOT NULL;")
            cursor.execute("ALTER TABLE b2b_budget_request ALTER COLUMN employee_id DROP NOT NULL;")
            cursor.execute("ALTER TABLE b2b_budget_request ADD COLUMN IF NOT EXISTS department_id BIGINT REFERENCES b2b_department(id) ON DELETE CASCADE;")
            cursor.execute("ALTER TABLE b2b_budget_request ADD COLUMN IF NOT EXISTS review_description TEXT;")
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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS b2b_lead_request (
                    id BIGSERIAL PRIMARY KEY,
                    full_name VARCHAR(200) NOT NULL,
                    company_name VARCHAR(200) NOT NULL,
                    email VARCHAR(254) NOT NULL,
                    phone_number VARCHAR(20) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created b2b_lead_request")

            self._create_workspace_tables(cursor)

        self.stdout.write(self.style.SUCCESS("B2B tables created successfully."))

    def _create_workspace_tables(self, cursor):
        """Tables behind the B2B mobile workspace (`/api/b2b/workspace/`).

        Everything here is scoped to a company and authored by a
        ``b2b_employee`` — the mobile app's identity is always an employee row,
        including for the owner (see ``ensure_workspace_employee``)."""

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_task (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                title VARCHAR(300) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status VARCHAR(20) NOT NULL DEFAULT 'todo',
                priority VARCHAR(20) NOT NULL DEFAULT 'medium',
                project VARCHAR(200),
                due_date TIMESTAMPTZ,
                author_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # The list screen always filters by company and sorts by deadline.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_task_company_due_idx "
            "ON b2b_task (company_id, due_date);"
        )
        self.stdout.write("  Created b2b_task")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_task_assignee (
                id BIGSERIAL PRIMARY KEY,
                task_id BIGINT NOT NULL REFERENCES b2b_task(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (task_id, employee_id)
            );
        """)
        # "Which tasks am I on?" is the employee role's entire task list.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_task_assignee_employee_idx "
            "ON b2b_task_assignee (employee_id);"
        )
        self.stdout.write("  Created b2b_task_assignee")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_task_subtask (
                id BIGSERIAL PRIMARY KEY,
                task_id BIGINT NOT NULL REFERENCES b2b_task(id) ON DELETE CASCADE,
                title VARCHAR(300) NOT NULL,
                is_done BOOLEAN NOT NULL DEFAULT FALSE,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_task_subtask_task_idx "
            "ON b2b_task_subtask (task_id, position);"
        )
        self.stdout.write("  Created b2b_task_subtask")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_task_comment (
                id BIGSERIAL PRIMARY KEY,
                task_id BIGINT NOT NULL REFERENCES b2b_task(id) ON DELETE CASCADE,
                author_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_task_comment_task_idx "
            "ON b2b_task_comment (task_id, created_at);"
        )
        self.stdout.write("  Created b2b_task_comment")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_calendar_event (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                title VARCHAR(300) NOT NULL,
                event_type VARCHAR(20) NOT NULL DEFAULT 'meeting',
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                all_day BOOLEAN NOT NULL DEFAULT FALSE,
                location VARCHAR(300),
                notes TEXT,
                author_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # The calendar always loads one month window at a time.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_calendar_event_company_start_idx "
            "ON b2b_calendar_event (company_id, starts_at);"
        )
        self.stdout.write("  Created b2b_calendar_event")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_calendar_participant (
                id BIGSERIAL PRIMARY KEY,
                event_id BIGINT NOT NULL REFERENCES b2b_calendar_event(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (event_id, employee_id)
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_calendar_participant_employee_idx "
            "ON b2b_calendar_participant (employee_id);"
        )
        self.stdout.write("  Created b2b_calendar_participant")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_chat_thread (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                group_name VARCHAR(200),
                created_by BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                last_message_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        self.stdout.write("  Created b2b_chat_thread")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_chat_member (
                id BIGSERIAL PRIMARY KEY,
                thread_id BIGINT NOT NULL REFERENCES b2b_chat_thread(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                is_pinned BOOLEAN NOT NULL DEFAULT FALSE,
                is_muted BOOLEAN NOT NULL DEFAULT FALSE,
                last_read_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (thread_id, employee_id)
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_chat_member_employee_idx "
            "ON b2b_chat_member (employee_id);"
        )
        self.stdout.write("  Created b2b_chat_member")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_chat_message (
                id BIGSERIAL PRIMARY KEY,
                thread_id BIGINT NOT NULL REFERENCES b2b_chat_thread(id) ON DELETE CASCADE,
                sender_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # Room history pages backwards from the newest message.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_chat_message_thread_idx "
            "ON b2b_chat_message (thread_id, id DESC);"
        )
        self.stdout.write("  Created b2b_chat_message")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_lead (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                author_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                company_name VARCHAR(300) NOT NULL,
                contact_full_name VARCHAR(300) NOT NULL,
                contact_phone VARCHAR(20) NOT NULL,
                product_name VARCHAR(300) NOT NULL,
                quantity NUMERIC(12, 2) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'new',
                claimed_by_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                claimed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # The board always filters by company and status.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_lead_company_status_idx "
            "ON b2b_workspace_lead (company_id, status);"
        )
        self.stdout.write("  Created b2b_workspace_lead")
