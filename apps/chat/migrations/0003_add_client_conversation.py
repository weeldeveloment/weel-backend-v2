from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0002_message_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE chat_conversation ADD COLUMN client_user_id BIGINT NULL;
            CREATE INDEX IF NOT EXISTS chat_conversation_client_updated_idx
                ON chat_conversation (client_user_id, updated_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS chat_conversation_admin_client_unique
                ON chat_conversation (admin_user_id, client_user_id)
                WHERE client_user_id IS NOT NULL;
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS chat_conversation_client_updated_idx;
            DROP INDEX IF EXISTS chat_conversation_admin_client_unique;
            ALTER TABLE chat_conversation DROP COLUMN IF EXISTS client_user_id;
            """,
        ),
    ]
