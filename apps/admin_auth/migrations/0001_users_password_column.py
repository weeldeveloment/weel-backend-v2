from django.db import migrations


# "Verify the admin password on sign-in" (d7251bd) started reading and writing
# users.password, but nothing ever created the column: the unit tests run on a
# scaffold that already has it (conftest._USERS_TABLE_DDL), so the gap only
# showed in production — `manage.py set_admin_password` and every admin login
# failed with "column "password" of relation "users" does not exist"
# (2026-09-09).
#
# It lives here, not under apps/users: that app is not in INSTALLED_APPS, so
# its migrations never run. apps.admin_auth is installed (for the management
# command), which makes this the one place a migration for the admin password
# actually executes on deploy. Raw SQL, idempotent, PostgreSQL only — the
# `users` table has no model, and the unit-test scaffold is SQLite.
def add_password_column_to_users(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    with connection.cursor() as cursor:
        # `users` is raw SQL owned by the (not yet committed) schema baseline, and
        # bootstrap_schema runs migrations *before* that baseline. On an empty
        # database — CI, a fresh environment — the table is not there yet, and
        # whatever creates it later (the baseline, the conftest scaffold) already
        # carries the column. Only an existing table needs the ALTER.
        cursor.execute("SELECT to_regclass('public.users')")
        if cursor.fetchone()[0] is None:
            return
        cursor.execute("""
            ALTER TABLE public.users
            ADD COLUMN IF NOT EXISTS password varchar(128) NULL;
        """)


def remove_password_column_from_users(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.users')")
        if cursor.fetchone()[0] is None:
            return
        cursor.execute("ALTER TABLE public.users DROP COLUMN IF EXISTS password;")


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunPython(
            add_password_column_to_users,
            remove_password_column_from_users,
        ),
    ]
