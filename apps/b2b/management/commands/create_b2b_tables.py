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
        self.stdout.write("  Created b2b_workspace_file")

        # Set/cleared by WorkspaceTaskStatusView whenever a task's status
        # transitions to/from "done" — `updated_at` moves on every edit, so it
        # can't tell whether a done task was ever touched again after finishing.
        # Employee-of-the-month's "on time" rate depends on this timestamp.
        cursor.execute("""
            ALTER TABLE b2b_task ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
        """)

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

    def _create_mail_tables(self, cursor):
        """Corporate mail (`/api/b2b/workspace/mail/`).

        The mail server (Mailcow) is the system of record for the messages
        themselves; these tables are the copy the web dashboard and the phone
        read, so neither has to speak IMAP. A row here is only ever reachable
        through the mailbox that owns it, and a mailbox belongs to exactly one
        employee — that is what keeps one company out of another's mail.
        """

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_domain (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                domain VARCHAR(253) NOT NULL UNIQUE,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                dkim_selector VARCHAR(63) NOT NULL DEFAULT 'weel',
                dkim_public_key TEXT,
                mx_ok BOOLEAN NOT NULL DEFAULT FALSE,
                spf_ok BOOLEAN NOT NULL DEFAULT FALSE,
                dkim_ok BOOLEAN NOT NULL DEFAULT FALSE,
                dmarc_ok BOOLEAN NOT NULL DEFAULT FALSE,
                last_error TEXT,
                last_checked_at TIMESTAMPTZ,
                verified_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # A company's mail settings screen looks its domains up by company.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_domain_company_idx "
            "ON b2b_mail_domain (company_id);"
        )
        self.stdout.write("  Created b2b_mail_domain")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mailbox (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                domain_id BIGINT NOT NULL REFERENCES b2b_mail_domain(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                address VARCHAR(320) NOT NULL UNIQUE,
                local_part VARCHAR(64) NOT NULL,
                display_name VARCHAR(200),
                smtp_password_enc TEXT NOT NULL,
                quota_bytes BIGINT NOT NULL DEFAULT 2147483648,
                daily_send_limit INTEGER NOT NULL DEFAULT 200,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_seen_uid BIGINT NOT NULL DEFAULT 0,
                uid_validity BIGINT,
                last_sync_at TIMESTAMPTZ,
                sync_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (employee_id)
            );
        """)
        # The sync beat walks every active mailbox; `/mail/me/` looks one up by
        # the signed-in employee.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mailbox_active_idx "
            "ON b2b_mailbox (is_active, last_sync_at);"
        )
        self.stdout.write("  Created b2b_mailbox")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_thread (
                id BIGSERIAL PRIMARY KEY,
                mailbox_id BIGINT NOT NULL REFERENCES b2b_mailbox(id) ON DELETE CASCADE,
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
        # The inbox list is "this mailbox, this folder, newest first".
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_thread_mailbox_idx "
            "ON b2b_mail_thread (mailbox_id, folder, last_message_at DESC);"
        )
        # Mail with no References header is threaded on the normalised subject.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_thread_subject_key_idx "
            "ON b2b_mail_thread (mailbox_id, subject_key);"
        )
        self.stdout.write("  Created b2b_mail_thread")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_mail_message (
                id BIGSERIAL PRIMARY KEY,
                thread_id BIGINT NOT NULL REFERENCES b2b_mail_thread(id) ON DELETE CASCADE,
                mailbox_id BIGINT NOT NULL REFERENCES b2b_mailbox(id) ON DELETE CASCADE,
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
        # globally unique per RFC 5322, but only within one mailbox's copy —
        # two employees on the same thread each keep their own row.
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_mail_message_dedup_idx "
            "ON b2b_mail_message (mailbox_id, message_id_header) "
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
                mailbox_id BIGINT NOT NULL REFERENCES b2b_mailbox(id) ON DELETE CASCADE,
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
                mailbox_id BIGINT NOT NULL REFERENCES b2b_mailbox(id) ON DELETE CASCADE,
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
        # The per-mailbox daily send limit counts rows in this window.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_mail_outbox_mailbox_idx "
            "ON b2b_mail_outbox (mailbox_id, created_at DESC);"
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
