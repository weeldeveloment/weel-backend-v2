from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("property", "0010_add_room_columns_to_cottage"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE public.apartment
            ADD COLUMN IF NOT EXISTS price_per_person numeric(12,2);
            """,
            reverse_sql="""
            ALTER TABLE public.apartment
            DROP COLUMN IF EXISTS price_per_person;
            """,
        ),
    ]