from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("property", "0009_create_cottage_price_table"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE IF EXISTS public.cottage
            ADD COLUMN IF NOT EXISTS guests integer NULL,
            ADD COLUMN IF NOT EXISTS rooms integer NULL,
            ADD COLUMN IF NOT EXISTS beds integer NULL,
            ADD COLUMN IF NOT EXISTS bathrooms integer NULL;
            """,
            reverse_sql="""
            ALTER TABLE IF EXISTS public.cottage
            DROP COLUMN IF EXISTS guests,
            DROP COLUMN IF EXISTS rooms,
            DROP COLUMN IF EXISTS beds,
            DROP COLUMN IF EXISTS bathrooms;
            """,
        ),
    ]
