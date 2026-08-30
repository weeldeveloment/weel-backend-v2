from django.core.management.base import BaseCommand
from django.db import connection, transaction


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
            # The handle somebody is found by — "@aziz", typed into the search
            # on "So'rov yuborish" instead of a name nobody spells the same way
            # twice. Nullable: every existing employee has none, and a roster
            # is imported from passports and phone numbers rather than from
            # people picking a handle.
            cursor.execute("""
                ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS username VARCHAR(50);
            """)
            # Unique per company rather than globally: two companies are
            # separate address books, and "@aziz" in one has nothing to do
            # with "@aziz" in the other. Partial, so the many rows with no
            # handle at all do not collide with each other.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS b2b_employee_username_idx
                ON b2b_employee (company_id, LOWER(username))
                WHERE username IS NOT NULL;
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

        # Everything that has happened to a task, company-wide: the events the
        # server writes itself (created, updated, status, (un)assigned,
        # deleted) and the notes employees type. task_id is nullable and
        # ON DELETE SET NULL, with task_title snapshotted at write time, so a
        # deleted task still reads as "X deleted" in the tasks-page feed after
        # the task row itself is gone. Mirrors b2b_workspace_lead_activity.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_task_activity (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                task_id BIGINT REFERENCES b2b_task(id) ON DELETE SET NULL,
                task_title VARCHAR(300) NOT NULL DEFAULT '',
                author_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                kind VARCHAR(20) NOT NULL DEFAULT 'comment',
                text TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_task_activity_task_idx "
            "ON b2b_task_activity (task_id, created_at DESC, id DESC);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_task_activity_company_idx "
            "ON b2b_task_activity (company_id, created_at DESC, id DESC);"
        )
        self.stdout.write("  Created b2b_task_activity")

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

        # One row per reminder actually sent, which is what stops an event
        # being reminded about twice. The beat task looks back over a few
        # minutes rather than only at the current one — a worker that was
        # restarted or busy would otherwise drop that minute's reminders
        # entirely — and this table is what makes that catch-up safe.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_calendar_reminder (
                id BIGSERIAL PRIMARY KEY,
                event_id BIGINT NOT NULL REFERENCES b2b_calendar_event(id) ON DELETE CASCADE,
                minutes_before INTEGER NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (event_id, minutes_before)
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_calendar_reminder_event_idx "
            "ON b2b_calendar_reminder (event_id);"
        )
        self.stdout.write("  Created b2b_calendar_reminder")

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
        # A group's own picture. The stored path, not a URL: the object may
        # move between storage backends, and every other file the workspace
        # keeps is addressed the same way — see `b2b_workspace_file.path`.
        cursor.execute(
            "ALTER TABLE b2b_chat_thread ADD COLUMN IF NOT EXISTS "
            "photo VARCHAR(500);"
        )
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

        # Who runs a group. Per membership rather than per employee: somebody
        # can run the sales room and be an ordinary member of the design one,
        # and a company-wide "chat admin" role would say neither.
        #
        # 'member' is the default, so every existing row becomes an ordinary
        # member and no one is silently handed control of a room they were
        # only ever in.
        cursor.execute(
            "ALTER TABLE b2b_chat_member ADD COLUMN IF NOT EXISTS "
            "role VARCHAR(20) NOT NULL DEFAULT 'member';"
        )
        # The person who opened the room runs it. Backfilled once — the
        # WHERE clause makes a second run a no-op, and it deliberately does
        # not touch a room whose creator has since been demoted by hand.
        cursor.execute("""
            UPDATE b2b_chat_member m
            SET role = 'admin'
            FROM b2b_chat_thread t
            WHERE m.thread_id = t.id
              AND t.group_name IS NOT NULL
              AND m.employee_id = t.created_by
              AND m.role = 'member'
              AND NOT EXISTS (
                  SELECT 1 FROM b2b_chat_member a
                  WHERE a.thread_id = t.id AND a.role = 'admin'
              );
        """)
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
        # Edited in place. The column is the whole feature: the text is
        # overwritten, and this is what lets the bubble say so — a message
        # that changed silently is worse than one that cannot be changed.
        cursor.execute(
            "ALTER TABLE b2b_chat_message ADD COLUMN IF NOT EXISTS "
            "edited_at TIMESTAMPTZ;"
        )

        # Forwarded. Points at the person who wrote the original rather than
        # at the message: the label says "Sardordan", and it has to keep
        # saying it after the original room is gone or the original message
        # deleted — which a reference to the message would not survive.
        cursor.execute(
            "ALTER TABLE b2b_chat_message ADD COLUMN IF NOT EXISTS "
            "forwarded_from_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL;"
        )

        # Pinned to the top of the room. On the message rather than on the
        # thread, so a room can hold more than one — and so unpinning is an
        # edit to the message that was pinned, not to the room.
        cursor.execute(
            "ALTER TABLE b2b_chat_message ADD COLUMN IF NOT EXISTS "
            "pinned_at TIMESTAMPTZ;"
        )
        cursor.execute(
            "ALTER TABLE b2b_chat_message ADD COLUMN IF NOT EXISTS "
            "pinned_by BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL;"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_chat_message_pinned_idx "
            "ON b2b_chat_message (thread_id, pinned_at DESC) "
            "WHERE pinned_at IS NOT NULL;"
        )

        # Searching a room. `text_pattern_ops` is wrong for this — the search
        # is a substring, not a prefix — so it wants a trigram index.
        #
        # Optional, deliberately. Creating the extension needs rights a managed
        # Postgres often does not hand out, and the search is an ILIKE either
        # way: without the index it reads the room's own messages, which is
        # thousands of rows, not millions. Failing the whole schema bootstrap
        # over a speedup would be the wrong trade — so this is attempted, and
        # the reason is written down if it cannot be.
        try:
            with transaction.atomic():
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS b2b_chat_message_text_idx "
                    "ON b2b_chat_message USING gin (text gin_trgm_ops);"
                )
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(
                f"  Skipped b2b_chat_message trigram index ({exc.__class__.__name__}); "
                "in-room search still works, unindexed"
            )
        self.stdout.write("  Created b2b_chat_message")

        # One emoji from one person on one message. The unique index is the
        # rule: tapping the same reaction twice takes it back rather than
        # stacking a second copy of it, and without the index two taps racing
        # each other would leave one behind that nothing can remove.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_chat_reaction (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL REFERENCES b2b_chat_message(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                emoji VARCHAR(16) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_chat_reaction_one_idx "
            "ON b2b_chat_reaction (message_id, employee_id, emoji);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_chat_reaction_message_idx "
            "ON b2b_chat_reaction (message_id);"
        )
        self.stdout.write("  Created b2b_chat_reaction")

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

        # A lead that came from outside. `external_id` is the other side's own
        # id for it — Meta's `leadgen_id` — and the unique index on it is what
        # makes ingestion idempotent: Meta retries a webhook it did not get a
        # 200 for, and without this the same enquiry would land on the board
        # three times. Partial, because every hand-raised lead has no external
        # id and a plain unique index would allow exactly one of them.
        for statement in (
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "integration_id BIGINT;",
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "external_id VARCHAR(120);",
            # Which lead-ad form filled it in — "Bahorgi aksiya", the name the
            # marketer gave the form. Printed on the card so a salesperson
            # knows what the customer was answering.
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "external_form_name VARCHAR(300);",
            # Everything the form asked that does not map onto a lead column,
            # kept verbatim so nothing the customer typed is thrown away.
            "ALTER TABLE b2b_workspace_lead ADD COLUMN IF NOT EXISTS "
            "external_data JSONB;",
        ):
            cursor.execute(statement)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_workspace_lead_external_idx "
            "ON b2b_workspace_lead (company_id, source, external_id) "
            "WHERE external_id IS NOT NULL;"
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

        # ─── Integrations ─────────────────────────────────────────────────
        #
        # An outside service plugged into the workspace: today Meta's lead
        # ads, tomorrow whatever else fills the funnel. One row per
        # (company, provider) — the connection itself — with the pages it
        # watches hanging off it.
        #
        # The token is stored encrypted (`apps/b2b/integrations/crypto.py`).
        # It is a credential to somebody else's account that we hold on their
        # behalf, exactly like a mail account's, and no endpoint ever reads
        # one back out.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_integration (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                provider VARCHAR(30) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'connected',
                -- Who at Meta authorised us: their user id and name, so the
                -- screen can say whose account this hangs off.
                account_id VARCHAR(120),
                account_name VARCHAR(300),
                access_token_enc TEXT,
                token_expires_at TIMESTAMPTZ,
                scopes TEXT NOT NULL DEFAULT '',
                connected_by_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                connected_at TIMESTAMPTZ,
                last_sync_at TIMESTAMPTZ,
                last_error TEXT,
                lead_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_integration_company_idx "
            "ON b2b_integration (company_id, provider);"
        )

        # A workspace may connect through **its own** Meta app rather than
        # ours. Both models are real and they answer different problems:
        #
        #  * Ours (the settings) is the normal path. One Facebook app serves
        #    every customer, nobody configures anything, and a company that
        #    just wants their leads in the funnel gets them.
        #  * Theirs (these columns) is for the company that cannot use ours —
        #    while our app is still in Meta's review and only its testers can
        #    authorise it, or a customer whose policy is that the advertising
        #    data never leaves an app they own.
        #
        # Null means "use ours", so this is additive: nothing changes for the
        # workspaces that never touch it. The secret is encrypted like every
        # other credential here and no endpoint reads it back out.
        #
        # `webhook_verify_token` is the string Meta quotes back once, when
        # *their* app's webhook is configured. It has to be per company for
        # the same reason the secret does — their app, their token — and it is
        # generated rather than typed so nobody picks "12345".
        for statement in (
            "ALTER TABLE b2b_integration ADD COLUMN IF NOT EXISTS "
            "app_id VARCHAR(64);",
            "ALTER TABLE b2b_integration ADD COLUMN IF NOT EXISTS "
            "app_secret_enc TEXT;",
            "ALTER TABLE b2b_integration ADD COLUMN IF NOT EXISTS "
            "webhook_verify_token VARCHAR(120);",
        ):
            cursor.execute(statement)
        # The webhook is one URL for every app that posts to it, so the
        # subscription handshake has to be able to find a company by the token
        # it was quoted — see `MetaWebhookView.get`.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_integration_verify_token_idx "
            "ON b2b_integration (webhook_verify_token) "
            "WHERE webhook_verify_token IS NOT NULL;"
        )
        self.stdout.write("  Created b2b_integration")

        # One Facebook page (or the Instagram account behind it) whose forms
        # feed this workspace. `page_id` is unique on its own and not per
        # company: Meta addresses a webhook by page, and two workspaces
        # claiming the same page would make "whose lead is this" unanswerable.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_integration_page (
                id BIGSERIAL PRIMARY KEY,
                integration_id BIGINT NOT NULL
                    REFERENCES b2b_integration(id) ON DELETE CASCADE,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                page_id VARCHAR(120) NOT NULL,
                page_name VARCHAR(300) NOT NULL DEFAULT '',
                -- The page access token, which is what actually reads a lead.
                -- Separate from the user token above: a page token issued
                -- against a long-lived user token does not expire.
                access_token_enc TEXT,
                -- Whether the workspace wants this page's leads. Turning it
                -- off leaves the row (and Meta's subscription) alone and
                -- simply stops the ingest — reconnecting is not required to
                -- pause one page of several.
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                subscribed BOOLEAN NOT NULL DEFAULT FALSE,
                lead_count INTEGER NOT NULL DEFAULT 0,
                last_lead_at TIMESTAMPTZ,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_integration_page_id_idx "
            "ON b2b_integration_page (page_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_integration_page_company_idx "
            "ON b2b_integration_page (company_id, is_active);"
        )
        self.stdout.write("  Created b2b_integration_page")

        # The delivery log. A row is written *before* the lead is fetched, so
        # a webhook that arrives twice while the first is still being handled
        # is refused by the unique index rather than racing it.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_integration_event (
                id BIGSERIAL PRIMARY KEY,
                provider VARCHAR(30) NOT NULL,
                external_id VARCHAR(120) NOT NULL,
                company_id BIGINT REFERENCES b2b_company(id) ON DELETE CASCADE,
                page_id VARCHAR(120),
                lead_id BIGINT REFERENCES b2b_workspace_lead(id) ON DELETE SET NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'received',
                error TEXT,
                payload JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_integration_event_idx "
            "ON b2b_integration_event (provider, external_id);"
        )
        self.stdout.write("  Created b2b_integration_event")

        cursor.execute(
            "ALTER TABLE b2b_workspace_lead "
            "DROP CONSTRAINT IF EXISTS b2b_workspace_lead_integration_fk;"
        )
        cursor.execute(
            "ALTER TABLE b2b_workspace_lead "
            "ADD CONSTRAINT b2b_workspace_lead_integration_fk "
            "FOREIGN KEY (integration_id) REFERENCES b2b_integration(id) "
            "ON DELETE SET NULL;"
        )

        # Every byte the company stores is a row here, whatever put it there —
        # the shared drive, a photo sent in a chat, a generated voucher. That
        # is what makes the 5 GB quota a single SUM over one table rather than
        # a counter that has to be kept in step with three upload paths and
        # drifts the first time one of them forgets.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_folder (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                author_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                name VARCHAR(120) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_folder_company_idx "
            "ON b2b_workspace_folder (company_id, created_at DESC);"
        )
        self.stdout.write("  Created b2b_workspace_folder")

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
        # Which drawer of the drive a file was put in, if any. NULL means the
        # drive itself — most files — and a deleted folder sets it back to
        # NULL rather than taking the files with it: emptying a shelf is not
        # the same act as throwing out what was on it.
        cursor.execute(
            "ALTER TABLE b2b_workspace_file ADD COLUMN IF NOT EXISTS "
            "folder_id BIGINT REFERENCES b2b_workspace_folder(id) ON DELETE SET NULL;"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_file_folder_idx "
            "ON b2b_workspace_file (folder_id) WHERE folder_id IS NOT NULL;"
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

        # ─── Organisations, and lending people between workspaces ────────────
        #
        # A word on names, because the code and the product disagree and it is
        # better said once here than guessed at forty times:
        #
        #   product "workspace"  →  a `b2b_company` row
        #   product "company"    →  a `b2b_org` row, which groups those
        #
        # Every operational table in this schema is keyed by `company_id`, and
        # that key already means exactly what the product calls a workspace:
        # its own leads, its own tasks, its own roster, its own chats. Adding a
        # second `workspace_id` beside it would mean rewriting every one of the
        # two hundred-odd queries that scope by company for no behavioural
        # gain, and the migration would have to be right on all of them at
        # once. So the isolation boundary stays where it is, and the new level
        # is added *above* it.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_org (
                id BIGSERIAL PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                owner_user_id BIGINT REFERENCES b2b_user(id) ON DELETE SET NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("""
            ALTER TABLE b2b_company ADD COLUMN IF NOT EXISTS org_id
                BIGINT REFERENCES b2b_org(id) ON DELETE SET NULL;
        """)
        # The company's STIR / INN, asked for on the "Kompaniya yaratish"
        # screen and optional there. Stored as text rather than a number: it
        # is an identifier that may carry leading zeros, and nothing here does
        # arithmetic on it.
        cursor.execute("""
            ALTER TABLE b2b_org ADD COLUMN IF NOT EXISTS tax_id VARCHAR(20);
        """)
        # The company's join code — "W-8932" — which somebody types to be shown
        # the workspaces inside it.
        #
        # Not the same object as a workspace invite link and deliberately so.
        # A link is the workspace deciding in advance: it names one room, one
        # role and one set of modules, and taking it is immediate. A company
        # code decides nothing. It says only "these are our rooms" and every
        # door behind it still has to be asked through, which is why it is
        # safe to print on an onboarding sheet or say out loud to a new hire.
        #
        # Short and typable rather than a long random token, because it is
        # meant to be read off a screen and typed on a phone.
        cursor.execute("""
            ALTER TABLE b2b_org ADD COLUMN IF NOT EXISTS join_code VARCHAR(16);
        """)
        # Companies that existed before the code did, filled in before the
        # unique index goes on so a collision cannot fail the deploy.
        #
        # `id * 7919 mod 90000` is a bijection for every id this will ever see:
        # 7919 is prime and shares no factor with 90000, so no two ids below
        # 90000 land on the same code. Past that the index below is the
        # authority and `_free_join_code` is what issues new ones.
        cursor.execute("""
            UPDATE b2b_org
               SET join_code = 'W-' || LPAD((mod(id * 7919, 90000) + 10000)::text, 5, '0')
             WHERE join_code IS NULL;
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS b2b_org_join_code_idx
            ON b2b_org (UPPER(join_code)) WHERE join_code IS NOT NULL;
        """)
        # What the "Yangi workspace yaratish" screen collects beyond the name:
        # a blurb and one of a fixed set of icon keys the app draws its own
        # colour for — see `WorkspaceIcon` in the Flutter app. Free text, not
        # an enum column: the set of icons is the client's to grow without a
        # migration on this side.
        cursor.execute("""
            ALTER TABLE b2b_company ADD COLUMN IF NOT EXISTS description TEXT;
        """)
        cursor.execute("""
            ALTER TABLE b2b_company ADD COLUMN IF NOT EXISTS icon VARCHAR(20);
        """)
        # Every workspace that has no organisation yet gets one of its own,
        # named after it. That is what keeps this change invisible: an org of
        # one workspace can only ever see itself, which is precisely the
        # isolation every deployment has today.
        #
        # One row at a time, and paired by the id the insert returns. Matching
        # the two tables up by name afterwards would be wrong the moment a
        # deployment has two workspaces called "Filial" — and every deployment
        # eventually does.
        cursor.execute("SELECT id, name FROM b2b_company WHERE org_id IS NULL ORDER BY id")
        orphans = cursor.fetchall()
        for company_id, company_name in orphans:
            cursor.execute(
                "INSERT INTO b2b_org (name, created_at, updated_at) "
                "VALUES (%s, NOW(), NOW()) RETURNING id",
                [company_name or f"Kompaniya #{company_id}"],
            )
            org_id = cursor.fetchone()[0]
            cursor.execute(
                "UPDATE b2b_company SET org_id = %s WHERE id = %s", [org_id, company_id]
            )
        if orphans:
            self.stdout.write(f"  Gave {len(orphans)} workspace(s) an org of their own")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_company_org_idx ON b2b_company (org_id);"
        )
        self.stdout.write("  Created b2b_org")

        # A person seconded into a workspace gets an employee row there, the
        # same as anybody else — which is the whole point: every existing
        # query, every assignment, every chat membership keeps working with no
        # idea that this person's home is elsewhere.
        #
        # `home_employee_id` is the row they were hired into, and `is_guest`
        # is what keeps the login lookup off these rows: `find_employee_by_phone`
        # searches by phone across the whole table, so without this flag a
        # second row with the same number would be a coin toss over which
        # workspace somebody lands in when they sign in.
        cursor.execute("""
            ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS is_guest
                BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        cursor.execute("""
            ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS home_employee_id
                BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL;
        """)
        # A "ghost" is in the workspace without being on anybody's list.
        cursor.execute("""
            ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS is_hidden
                BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        self.stdout.write("  Extended b2b_employee for guests")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_request (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                from_employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                to_employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                message TEXT NOT NULL DEFAULT '',
                role VARCHAR(20) NOT NULL DEFAULT 'employee',
                modules JSONB NOT NULL DEFAULT '[]'::jsonb,
                starts_at TIMESTAMPTZ,
                ends_at TIMESTAMPTZ,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                decline_reason TEXT,
                responded_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_request_to_idx "
            "ON b2b_workspace_request (to_employee_id, status, created_at DESC);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_request_from_idx "
            "ON b2b_workspace_request (company_id, created_at DESC);"
        )
        # One live ask at a time per person per workspace. Without it, a lider
        # tapping send twice puts two identical rows in somebody's inbox and
        # accepting both creates two guest rows for one person.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS b2b_workspace_request_pending_idx
            ON b2b_workspace_request (company_id, to_employee_id)
            WHERE status = 'pending';
        """)
        self.stdout.write("  Created b2b_workspace_request")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_membership (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                home_employee_id BIGINT NOT NULL REFERENCES b2b_employee(id) ON DELETE CASCADE,
                request_id BIGINT REFERENCES b2b_workspace_request(id) ON DELETE SET NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'employee',
                modules JSONB NOT NULL DEFAULT '[]'::jsonb,
                starts_at TIMESTAMPTZ,
                ends_at TIMESTAMPTZ,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                ended_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS b2b_workspace_membership_employee_idx "
            "ON b2b_workspace_membership (employee_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_membership_home_idx "
            "ON b2b_workspace_membership (home_employee_id, is_active);"
        )
        self.stdout.write("  Created b2b_workspace_membership")

        # ─── Who → where → what ──────────────────────────────────────────────
        #
        # The TZ's access model. Roles are fixed in code (`access.Role`); what
        # each one *may do* is per workspace and editable, which is why it is a
        # table and not a constant. A workspace with no rows here falls back to
        # `access.default_access`, so this is configuration rather than a
        # prerequisite — nothing has to be seeded for a workspace to work.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_role (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                code VARCHAR(20) NOT NULL,
                modules JSONB NOT NULL DEFAULT '[]'::jsonb,
                permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                updated_by BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (company_id, code)
            );
        """)
        self.stdout.write("  Created b2b_workspace_role")

        # One person's own access, where it differs from their role's.
        #
        # NULL means "by role", which is the ordinary case and the first of the
        # two answers the invite screen offers. A list means "configure" — and
        # it replaces the role's list rather than adding to it, or inviting a
        # manager *without* the sales board would be impossible.
        cursor.execute("""
            ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS module_access JSONB;
        """)
        cursor.execute("""
            ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS permission_access JSONB;
        """)
        self.stdout.write("  Extended b2b_employee with per-person access")

        # ─── The Weel Account ────────────────────────────────────────────────
        #
        # One human, one account, however many workspaces they work in. The TZ
        # puts the phone and a globally unique username here rather than on a
        # roster row, and it has to be that way round: somebody permanently
        # employed by two workspaces has two `b2b_employee` rows and one
        # handle, so a unique index on the roster could never express it.
        #
        # `b2b_employee` stays what it always was — a membership: this person,
        # in this workspace, with this role.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_account (
                id BIGSERIAL PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                username VARCHAR(50),
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                photo VARCHAR(500),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        # Compared digits-only, because the same number is stored as
        # "+998 90 123 45 67" in one place and "998901234567" in another.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS b2b_account_phone_idx
            ON b2b_account (regexp_replace(phone, '[^0-9]', '', 'g'));
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS b2b_account_username_idx
            ON b2b_account (LOWER(username)) WHERE username IS NOT NULL;
        """)
        # This phone, addressable before it has a seat anywhere.
        #
        # `b2b_employee.fcm_token` cannot answer for somebody who has asked to
        # join and is waiting: there is no employee row until the request is
        # accepted, which is exactly the moment they need to be told. So the
        # account carries its own token, registered as soon as registration
        # finishes, and the two live side by side — the employee one addresses
        # a person *in* a workspace, this one addresses a person who is not in
        # one yet.
        cursor.execute("""
            ALTER TABLE b2b_account ADD COLUMN IF NOT EXISTS fcm_token VARCHAR(500);
        """)
        cursor.execute("""
            ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS account_id
                BIGINT REFERENCES b2b_account(id) ON DELETE SET NULL;
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_employee_account_idx "
            "ON b2b_employee (account_id);"
        )

        # Backfill: an account per distinct number already on a roster, and
        # every row carrying that number pointed at it. Guests included — the
        # whole point is that the guest row and the home row are one person.
        cursor.execute("""
            INSERT INTO b2b_account (phone, first_name, photo, created_at, updated_at)
            SELECT DISTINCT ON (regexp_replace(e.phone, '[^0-9]', '', 'g'))
                   e.phone, e.full_name, e.photo, NOW(), NOW()
              FROM b2b_employee e
             WHERE e.phone IS NOT NULL
               AND regexp_replace(e.phone, '[^0-9]', '', 'g') <> ''
               AND NOT EXISTS (
                   SELECT 1 FROM b2b_account a
                    WHERE regexp_replace(a.phone, '[^0-9]', '', 'g')
                        = regexp_replace(e.phone, '[^0-9]', '', 'g')
               )
             ORDER BY regexp_replace(e.phone, '[^0-9]', '', 'g'), e.id;
        """)
        cursor.execute("""
            UPDATE b2b_employee e
               SET account_id = a.id
              FROM b2b_account a
             WHERE e.account_id IS NULL
               AND e.phone IS NOT NULL
               AND regexp_replace(a.phone, '[^0-9]', '', 'g')
                 = regexp_replace(e.phone, '[^0-9]', '', 'g');
        """)
        # Handles move up to the account they belong to. The lowest employee id
        # wins a collision, which is the oldest row — the one somebody has had
        # longest and is known by.
        cursor.execute("""
            UPDATE b2b_account a
               SET username = LOWER(src.username)
              FROM (
                SELECT DISTINCT ON (account_id) account_id, username
                  FROM b2b_employee
                 WHERE username IS NOT NULL AND account_id IS NOT NULL
                 ORDER BY account_id, id
              ) src
             WHERE a.id = src.account_id
               AND a.username IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM b2b_account other
                    WHERE LOWER(other.username) = LOWER(src.username)
                      AND other.id <> a.id
               );
        """)
        # The old per-workspace index is wrong under the new model: one person
        # employed by two workspaces holds one handle and would collide with
        # themselves.
        cursor.execute("DROP INDEX IF EXISTS b2b_employee_username_idx;")
        self.stdout.write("  Created b2b_account and linked the roster to it")

        # ─── Invitations, and asking to join ─────────────────────────────────
        #
        # An invite is a link: role, module access and an expiry, handed out by
        # somebody who may invite. The token is what the link carries, so it is
        # generated from `secrets` and never derived from the row's id.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_workspace_invite (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                token VARCHAR(64) NOT NULL UNIQUE,
                role VARCHAR(20) NOT NULL DEFAULT 'employee',
                modules JSONB,
                permissions JSONB,
                expires_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ,
                created_by BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                accepted_by BIGINT REFERENCES b2b_account(id) ON DELETE SET NULL,
                accepted_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_workspace_invite_company_idx "
            "ON b2b_workspace_invite (company_id, created_at DESC);"
        )
        # An invite to one conversation rather than to the workspace. Same
        # table, because it is the same object — a token, an expiry, a revoke
        # and one accept — and splitting it would mean two of every query that
        # answers "is this link still good?".
        cursor.execute("""
            ALTER TABLE b2b_workspace_invite ADD COLUMN IF NOT EXISTS thread_id
                BIGINT REFERENCES b2b_chat_thread(id) ON DELETE CASCADE;
        """)
        cursor.execute("""
            ALTER TABLE b2b_workspace_invite ADD COLUMN IF NOT EXISTS is_chat_only
                BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        self.stdout.write("  Created b2b_workspace_invite")

        # Somebody asking to be let in, having found the workspace by its
        # handle. The mirror image of an invite: the workspace decides the role
        # and the modules, and what the asker chose is only a request for them.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_join_request (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                account_id BIGINT NOT NULL REFERENCES b2b_account(id) ON DELETE CASCADE,
                message TEXT NOT NULL DEFAULT '',
                wanted_modules JSONB,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                decline_reason TEXT,
                granted_role VARCHAR(20),
                granted_modules JSONB,
                decided_by BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                decided_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS b2b_join_request_pending_idx
            ON b2b_join_request (company_id, account_id) WHERE status = 'pending';
        """)
        self.stdout.write("  Created b2b_join_request")

        # A workspace's own handle, so it can be found by name at all.
        cursor.execute("""
            ALTER TABLE b2b_company ADD COLUMN IF NOT EXISTS slug VARCHAR(50);
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS b2b_company_slug_idx
            ON b2b_company (LOWER(slug)) WHERE slug IS NOT NULL;
        """)

        # ─── Chat-only membership ────────────────────────────────────────────
        #
        # Somebody invited to one conversation and nothing else. They still
        # need a row on the roster — every message references `b2b_employee(id)`
        # — so the flag is what keeps that row out of the roster, out of the
        # navigation and out of every module.
        cursor.execute("""
            ALTER TABLE b2b_employee ADD COLUMN IF NOT EXISTS is_chat_only
                BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        self.stdout.write("  Extended b2b_employee for chat-only members")

        # ─── Soft delete ─────────────────────────────────────────────────────
        #
        # Delete is not destroy. A removed task or deal keeps its id, its
        # links, its author and its history; it simply stops appearing.
        for table in ("b2b_task", "b2b_workspace_lead"):
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;"
            )
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deleted_by BIGINT "
                f"REFERENCES b2b_employee(id) ON DELETE SET NULL;"
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_not_deleted_idx "
                f"ON {table} (company_id) WHERE deleted_at IS NULL;"
            )
        self.stdout.write("  Added soft delete to tasks and leads")

        # ─── Audit ───────────────────────────────────────────────────────────
        #
        # Role changes, permission changes, deletions and restores. Append
        # only: nothing in the application updates or deletes a row here.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS b2b_audit_event (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL REFERENCES b2b_company(id) ON DELETE CASCADE,
                actor_employee_id BIGINT REFERENCES b2b_employee(id) ON DELETE SET NULL,
                action VARCHAR(60) NOT NULL,
                target_type VARCHAR(40),
                target_id BIGINT,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS b2b_audit_event_company_idx "
            "ON b2b_audit_event (company_id, created_at DESC);"
        )
        self.stdout.write("  Created b2b_audit_event")
