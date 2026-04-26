from django.db import migrations


def add_fcm_columns_to_users(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    with connection.cursor() as cursor:
        cursor.execute("""
            ALTER TABLE public.users
            ADD COLUMN IF NOT EXISTS fcm_token varchar(255) NULL,
            ADD COLUMN IF NOT EXISTS device_type varchar(10) NULL;
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS users_fcm_token_idx
            ON public.users (fcm_token)
            WHERE fcm_token IS NOT NULL;
        """)


def remove_fcm_columns_from_users(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    with connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS users_fcm_token_idx;")
        cursor.execute("""
            ALTER TABLE public.users
            DROP COLUMN IF EXISTS fcm_token,
            DROP COLUMN IF EXISTS device_type;
        """)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0006_alter_smslog_purpose"),
    ]

    operations = [
        migrations.RunPython(
            add_fcm_columns_to_users,
            remove_fcm_columns_from_users,
        ),
    ]
