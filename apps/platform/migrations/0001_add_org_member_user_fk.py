from django.db import migrations


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE platform_organization_member
            ADD CONSTRAINT platform_organization_member_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            """,
            reverse_sql="""
            ALTER TABLE platform_organization_member
            DROP CONSTRAINT IF EXISTS platform_organization_member_user_id_fkey;
            """,
        ),
    ]
