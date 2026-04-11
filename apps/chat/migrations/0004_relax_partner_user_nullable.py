from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0003_add_client_conversation"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'chat_conversation'
                      AND column_name = 'partner_user_id'
                ) THEN
                    ALTER TABLE chat_conversation
                    ALTER COLUMN partner_user_id DROP NOT NULL;
                ELSIF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'chat_conversation'
                      AND column_name = 'partner_id'
                ) THEN
                    ALTER TABLE chat_conversation
                    ALTER COLUMN partner_id DROP NOT NULL;
                END IF;
            END
            $$;
            """,
            reverse_sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'chat_conversation'
                      AND column_name = 'partner_user_id'
                ) THEN
                    ALTER TABLE chat_conversation
                    ALTER COLUMN partner_user_id SET NOT NULL;
                ELSIF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'chat_conversation'
                      AND column_name = 'partner_id'
                ) THEN
                    ALTER TABLE chat_conversation
                    ALTER COLUMN partner_id SET NOT NULL;
                END IF;
            END
            $$;
            """,
        ),
    ]
