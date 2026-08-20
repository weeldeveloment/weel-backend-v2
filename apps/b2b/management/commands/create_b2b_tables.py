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
            self._create_mail_tables(cursor)

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

        # Threaded replies. SET NULL rather than CASCADE: deleting a message
        # someone answered must not take their answer with it — the reply
        # survives and simply stops quoting anything, which is what every
        # other messenger does.
        cursor.execute(
            "ALTER TABLE b2b_chat_message ADD COLUMN IF NOT EXISTS "
            "reply_to_id BIGINT REFERENCES b2b_chat_message(id) ON DELETE SET NULL;"
        )
        self.stdout.write("  Created b2b_chat_message")

        # The company's customer directory. A lead is raised against a card
        # here, so the second deal with the same buyer reuses their details
        # rather than retyping them — and the funnel can say "2 ta bitim"
        # before anyone opens the card.
        #
        # The phone is the identity: names are typed differently every time
        # ("Aziz Karimov", "Karimov A.") and a company can have two of them,
        # but a number is a number. Hence the unique index rather than a
        # convention nobody enforces.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_customer (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                full_name VARCHAR(300) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                company_name VARCHAR(300),
                position VARCHAR(200),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_workspace_customer_phone_idx "
            "ON b2b_workspace_customer (company_id, phone);"
        )
        # The CRM detail card's "Kontakt ma'lumotlari" needs an email and an
        # address on the customer themselves, not just on whichever lead
        # happened to collect one — backfilled from a lead's contact fields
        # the same way the name and company are.
        for statement in (
            "ALTER TABLE b2b_workspace_customer ADD COLUMN IF NOT EXISTS "
            "email VARCHAR(254);",
            "ALTER TABLE b2b_workspace_customer ADD COLUMN IF NOT EXISTS "
            "address TEXT;",
        ):
            cursor.execute(statement)
        self.stdout.write("  Created b2b_workspace_customer")

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

        # A lead started as company + contact + one product. The mobile funnel
        # needs more of the deal than that, so:
        #
        #  * `stage` is where the lead sits inside `status`. The three statuses
        #    are the board's columns; the stage is the step the salesperson
        #    actually moves through, and "won"/"lost" are what close a lead.
        #  * `source` is where it came from — the funnel labels every card with
        #    it and there was nowhere to record it.
        #  * `amount` is the deal's value, kept as the sum of the lead's line
        #    items so the card and the list can show money without joining.
        #  * the extra `contact_*` columns are the rest of the card a
        #    salesperson actually calls from.
        for statement in (
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "stage VARCHAR(30) NOT NULL DEFAULT 'new';",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "source VARCHAR(30) NOT NULL DEFAULT 'manual';",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "amount NUMERIC(14, 2) NOT NULL DEFAULT 0;",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "contact_position VARCHAR(200);",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "contact_email VARCHAR(254);",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "contact_address TEXT;",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "completed_at TIMESTAMPTZ;",
            # Why a deal was lost, and the salesperson's own words beside it.
            # A closed-lost lead without a reason is a number nobody can act
            # on, so `set_lead_stage` refuses to write `lost` without one.
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "lost_reason VARCHAR(30);",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "lost_note TEXT;",
            # Who the deal is with. Nullable, and SET NULL rather than CASCADE:
            # every lead raised before the directory existed has none, and
            # removing a customer card must not take their deal history with it.
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "customer_id BIGINT REFERENCES b2b_workspace_customer(id) ON DELETE SET NULL;",
        ):
            cursor.execute(statement)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_lead_customer_idx "
            "ON b2b_workspace_lead (customer_id) WHERE customer_id IS NOT NULL;"
        )
        self.stdout.write("  Created b2b_workspace_lead")

        # What the deal is actually made of — "CRM tizimi — Bazaviy paket,
        # 3 oy, 9 000 000". The lead's own `product_name`/`quantity` stay as
        # the headline ask; these are the priced lines under it, and their sum
        # is mirrored onto `b2b_workspace_lead.amount`.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_lead_item (
                id BIGSERIAL PRIMARY KEY,
                lead_id BIGINT NOT NULL REFERENCES b2b_workspace_lead(id) ON DELETE CASCADE,
                name VARCHAR(300) NOT NULL,
                unit VARCHAR(100) NOT NULL DEFAULT '',
                amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_lead_item_lead_idx "
            "ON b2b_workspace_lead_item (lead_id, position, id);"
        )
        self.stdout.write("  Created b2b_workspace_lead_item")

        # Everything that has happened to the lead, in one place: the events
        # the server writes itself (created, claimed, stage moved, completed)
        # and the notes employees type. One table rather than two, because the
        # screen shows them as one list and paging two sources in step is work
        # nobody needs.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_lead_activity (
                id BIGSERIAL PRIMARY KEY,
                lead_id BIGINT NOT NULL REFERENCES b2b_workspace_lead(id) ON DELETE CASCADE,
                author_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                kind VARCHAR(20) NOT NULL DEFAULT 'comment',
                text TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_lead_activity_lead_idx "
            "ON b2b_workspace_lead_activity (lead_id, created_at DESC, id DESC);"
        )
        self.stdout.write("  Created b2b_workspace_lead_activity")

        # A task raised off a lead. Nullable and ON DELETE SET NULL: the great
        # majority of tasks have nothing to do with a lead, and deleting a lead
        # must not take somebody's task with it.
        cursor.execute(
            "ALTER TABLE b2b_task ADD COLUMN IF NOT EXISTS lead_id BIGINT "
            "REFERENCES b2b_workspace_lead(id) ON DELETE SET NULL;"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_task_lead_idx "
            "ON b2b_task (lead_id) WHERE lead_id IS NOT NULL;"
        )

        # Every byte the company stores is a row here, whatever put it there —
        # the shared drive, a photo sent in a chat, a generated voucher. That
        # is what makes the 5 GB quota a single SUM over one table rather than
        # a counter that has to be kept in step with three upload paths and
        # drifts the first time one of them forgets.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_file (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                author_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                name VARCHAR(300) NOT NULL,
                path VARCHAR(500) NOT NULL,
                size BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_file_company_idx "
            "ON b2b_workspace_file (company_id, created_at DESC);"
        )

        # Where the bytes came from: 'file' (shared drive), 'chat' (an image or
        # video sent in a thread), 'voucher' (a generated trip document).
        # Defaulted rather than backfilled — every row that existed before this
        # column was a drive upload.
        cursor.execute(
            "ALTER TABLE b2b_workspace_file "
            "ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'file';"
        )
        cursor.execute(
            "ALTER TABLE b2b_workspace_file "
            "ADD COLUMN IF NOT EXISTS content_type VARCHAR(120);"
        )
        # How long a voice message runs, in milliseconds. Stored rather than
        # read off the file: the thread renders a duration under every voice
        # bubble, and working it out client-side would mean downloading every
        # clip in the history just to label them.
        cursor.execute(
            "ALTER TABLE b2b_workspace_file "
            "ADD COLUMN IF NOT EXISTS duration_ms INTEGER;"
        )
        # A chat attachment dies with its message. ON DELETE CASCADE is what
        # keeps the quota honest: deleting a thread has to give the bytes back,
        # and a nightly reconciliation job would be the alternative.
        cursor.execute(
            "ALTER TABLE b2b_workspace_file ADD COLUMN IF NOT EXISTS "
            "message_id BIGINT REFERENCES b2b_chat_message(id) ON DELETE CASCADE;"
        )
        cursor.execute(
            "ALTER TABLE b2b_workspace_file ADD COLUMN IF NOT EXISTS "
            "trip_id BIGINT REFERENCES b2b_business_trip(id) ON DELETE CASCADE;"
        )
        # A voice note recorded while a task was being written. Same table as
        # every other upload because the quota is one SUM over it, and CASCADE
        # for the same reason a chat attachment cascades: deleting the task has
        # to give the bytes back.
        cursor.execute(
            "ALTER TABLE b2b_workspace_file ADD COLUMN IF NOT EXISTS "
            "task_id BIGINT REFERENCES b2b_task(id) ON DELETE CASCADE;"
        )
        # The quota reads SUM(size) per company on every upload, and the
        # drive list filters to kind='file'. Both go through this.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_file_company_kind_idx "
            "ON b2b_workspace_file (company_id, kind);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_file_message_idx "
            "ON b2b_workspace_file (message_id) WHERE message_id IS NOT NULL;"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_file_task_idx "
            "ON b2b_workspace_file (task_id) WHERE task_id IS NOT NULL;"
        )
        self.stdout.write("  Created b2b_workspace_file")

        # The workspace dropped the fourth task status. Anything parked in
        # "review" is work in progress that was never finished, so that is
        # where it goes — left alone it would sit in a status no screen can
        # show or move it out of.
        cursor.execute(
            "UPDATE b2b_task SET status = 'in_progress' WHERE status = 'review';"
        )

        # Set/cleared by WorkspaceTaskStatusView whenever a task's status
        # transitions to/from "done" — `updated_at` moves on every edit, so it
        # can't tell whether a done task was ever touched again after finishing.
        # Employee-of-the-month's "on time" rate depends on this timestamp.
        cursor.execute("""
            ALTER TABLE b2b_task ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
        """)

        # One row per employee per day. Attendance is a fact about a date, and
        # the UNIQUE key is what makes writing it idempotent: an employee
        # tapping check-in twice, or a manager correcting a status, updates the
        # day rather than adding a second contradictory row.
        #
        # `work_date` is a DATE, not a timestamp: "was Aziz in on the 14th" has
        # one answer, and storing an instant would make it depend on the
        # reader's timezone.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_attendance (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                work_date DATE NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'present',
                checked_in_at TIMESTAMPTZ,
                reason VARCHAR(200),
                -- Null when the employee checked themselves in; set when a
                -- manager recorded it for them. Worth keeping: "who said I was
                -- absent" is the first question anyone asks.
                marked_by_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (employee_id, work_date)
            );
        """)
        # The roll call reads one company on one day, every time.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_attendance_company_date_idx "
            "ON b2b_attendance (company_id, work_date);"
        )
        # Where a geofenced check-in actually happened, for the same reason
        # `marked_by_id` is kept: a rejected or accepted check-in should have
        # something to point at besides the employee's word.
        cursor.execute("""
            ALTER TABLE b2b_attendance ADD COLUMN IF NOT EXISTS check_in_latitude NUMERIC(9,6);
        """)
        cursor.execute("""
            ALTER TABLE b2b_attendance ADD COLUMN IF NOT EXISTS check_in_longitude NUMERIC(9,6);
        """)
        self.stdout.write("  Created b2b_attendance")

        # One row per company: the office point and radius a check-in is
        # measured against. `is_enabled` is kept separate from having
        # coordinates at all — an owner picking a point on the map before
        # switching geofencing on must not have it enforced early, and
        # switching it off later must not lose the point for next time.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_attendance_location (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL UNIQUE REFERENCES b2b_company(id) ON DELETE CASCADE,
                is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                latitude NUMERIC(9,6),
                longitude NUMERIC(9,6),
                radius_meters INTEGER NOT NULL DEFAULT 200,
                updated_by_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        self.stdout.write("  Created b2b_attendance_location")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_employee_of_month (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                selected_by_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                selected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (company_id, year, month)
            );
        """)
        self.stdout.write("  Created b2b_employee_of_month")

        # The help desk. One flat log per employee rather than a thread table
        # plus a message table: an employee has exactly one conversation with
        # WEEL support and it is never forked, so a thread row would carry no
        # information the employee id does not already give.
        #
        # `is_staff` is which side wrote the line — the app puts the employee's
        # own words on the right and support's on the left, and that is the
        # only thing it has to know. `author_user_id` is the dashboard account
        # that answered, null for a line the employee wrote.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_support_message (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                is_staff BOOLEAN NOT NULL DEFAULT FALSE,
                author_user_id BIGINT REFERENCES b2b_user(id) ON DELETE SET NULL,
                read_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # The app reads one employee's log oldest-first; the admin inbox reads
        # a company's newest-first. Both are this index.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_support_message_employee_idx "
            "ON b2b_support_message (employee_id, created_at);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_support_message_company_idx "
            "ON b2b_support_message (company_id, created_at DESC);"
        )
        self.stdout.write("  Created b2b_support_message")

    def _create_mail_tables(self, cursor):
        """Mail inside the workspace (`/api/b2b/workspace/mail/`).

        We do not host mail. An employee connects an inbox they already have —
        their Gmail, their company address — and it appears in the chat section
        beside their colleagues. The provider stays the system of record; these
        tables are the copy the web dashboard and the phone read, so neither
        has to speak IMAP or hold a credential.

        Everything hangs off `b2b_mail_account`, which belongs to exactly one
        employee. That is what keeps one person out of another's mail, and one
        company out of another's.
        """

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_account (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                address VARCHAR(320) NOT NULL,
                display_name VARCHAR(200),
                provider VARCHAR(20) NOT NULL DEFAULT 'imap',
                auth_type VARCHAR(20) NOT NULL DEFAULT 'app_password',
                secret_enc TEXT NOT NULL,
                oauth_access_enc TEXT,
                oauth_expires_at TIMESTAMPTZ,
                imap_host VARCHAR(253) NOT NULL,
                imap_port INTEGER NOT NULL DEFAULT 993,
                smtp_host VARCHAR(253) NOT NULL,
                smtp_port INTEGER NOT NULL DEFAULT 587,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_seen_uid BIGINT NOT NULL DEFAULT 0,
                uid_validity BIGINT,
                last_sync_at TIMESTAMPTZ,
                sync_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                -- One person may connect several inboxes (a personal Gmail and
                -- a work address), but not the same one twice.
                UNIQUE (employee_id, address)
            );
        """)
        # The sync beat walks every active account, least-recently-synced
        # first; the chat screen looks them up by employee.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_account_employee_idx "
            "ON b2b_mail_account (employee_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_account_sync_idx "
            "ON b2b_mail_account (is_active, last_sync_at);"
        )
        self.stdout.write("  Created b2b_mail_account")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_thread (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES b2b_mail_account(id) ON DELETE CASCADE,
                subject VARCHAR(500) NOT NULL DEFAULT '',
                subject_key VARCHAR(500) NOT NULL DEFAULT '',
                snippet VARCHAR(500) NOT NULL DEFAULT '',
                folder VARCHAR(20) NOT NULL DEFAULT 'inbox',
                participants TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                unread_count INTEGER NOT NULL DEFAULT 0,
                is_starred BOOLEAN NOT NULL DEFAULT FALSE,
                last_message_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # The inbox list is "this account, this folder, newest first".
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_thread_account_idx "
            "ON b2b_mail_thread (account_id, folder, last_message_at DESC);"
        )
        # Mail with no References header is threaded on the normalised subject.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_thread_subject_key_idx "
            "ON b2b_mail_thread (account_id, subject_key);"
        )
        self.stdout.write("  Created b2b_mail_thread")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_message (
                id BIGSERIAL PRIMARY KEY,
                thread_id BIGINT NOT NULL REFERENCES b2b_mail_thread(id) ON DELETE CASCADE,
                account_id BIGINT NOT NULL REFERENCES b2b_mail_account(id) ON DELETE CASCADE,
                direction VARCHAR(10) NOT NULL DEFAULT 'inbound',
                status VARCHAR(20) NOT NULL DEFAULT 'delivered',
                imap_uid BIGINT,
                message_id_header VARCHAR(998),
                in_reply_to VARCHAR(998),
                references_header TEXT,
                from_address VARCHAR(320) NOT NULL DEFAULT '',
                from_name VARCHAR(300) NOT NULL DEFAULT '',
                subject VARCHAR(500) NOT NULL DEFAULT '',
                body_text TEXT NOT NULL DEFAULT '',
                body_html_sanitized TEXT NOT NULL DEFAULT '',
                has_attachments BOOLEAN NOT NULL DEFAULT FALSE,
                is_read BOOLEAN NOT NULL DEFAULT FALSE,
                is_starred BOOLEAN NOT NULL DEFAULT FALSE,
                error TEXT,
                sent_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # A thread pages backwards from its newest message, like chat rooms do.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_message_thread_idx "
            "ON b2b_mail_message (thread_id, id DESC);"
        )
        # Re-fetching the same UID must not duplicate a message. The header is
        # globally unique per RFC 5322, but only within one account's copy —
        # two colleagues on the same thread each keep their own row.
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_mail_message_dedup_idx "
            "ON b2b_mail_message (account_id, message_id_header) "
            "WHERE message_id_header IS NOT NULL;"
        )
        self.stdout.write("  Created b2b_mail_message")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_recipient (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL REFERENCES b2b_mail_message(id) ON DELETE CASCADE,
                kind VARCHAR(3) NOT NULL DEFAULT 'to',
                address VARCHAR(320) NOT NULL,
                name VARCHAR(300) NOT NULL DEFAULT ''
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_recipient_message_idx "
            "ON b2b_mail_recipient (message_id);"
        )
        self.stdout.write("  Created b2b_mail_recipient")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_attachment (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT REFERENCES b2b_mail_message(id) ON DELETE CASCADE,
                account_id BIGINT NOT NULL REFERENCES b2b_mail_account(id) ON DELETE CASCADE,
                filename VARCHAR(300) NOT NULL,
                content_type VARCHAR(200) NOT NULL DEFAULT 'application/octet-stream',
                size_bytes BIGINT NOT NULL DEFAULT 0,
                storage_key VARCHAR(500) NOT NULL,
                content_id VARCHAR(300),
                is_inline BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # `message_id` is null while a draft's upload is still unattached, so
        # the compose screen can upload before the message exists.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_attachment_message_idx "
            "ON b2b_mail_attachment (message_id);"
        )
        self.stdout.write("  Created b2b_mail_attachment")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_outbox (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES b2b_mail_account(id) ON DELETE CASCADE,
                message_id BIGINT REFERENCES b2b_mail_message(id) ON DELETE CASCADE,
                payload JSONB NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # The per-account daily send limit counts rows in this window.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_outbox_account_idx "
            "ON b2b_mail_outbox (account_id, created_at DESC);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_outbox_pending_idx "
            "ON b2b_mail_outbox (status, scheduled_at);"
        )
        self.stdout.write("  Created b2b_mail_outbox")

        # The `notification` table in apps/notification only recognises the
        # `client` and `partner` roles, and its rows are keyed to `users`. B2B
        # employees live in their own table, so they get their own feed rather
        # than a nullable-column fork of that one.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_notification (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                kind VARCHAR(30) NOT NULL,
                title VARCHAR(300) NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                is_read BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_notification_employee_idx "
            "ON b2b_notification (employee_id, created_at DESC);"
        )
        self.stdout.write("  Created b2b_notification")
