from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("property", "0007_remove_shaharcha_mahalla_from_property"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE IF EXISTS public.cottage
            ADD COLUMN IF NOT EXISTS region_id bigint NULL,
            ADD COLUMN IF NOT EXISTS district_id bigint NULL;

            UPDATE public.cottage c
            SET
                district_id = dp.district_id,
                region_id = d.region_id
            FROM public.district_prefecture dp
            LEFT JOIN public.district d ON d.id = dp.district_id
            WHERE c.prefecture_id = dp.prefecture_id
              AND (c.district_id IS NULL OR c.region_id IS NULL);
            """,
            reverse_sql="""
            ALTER TABLE IF EXISTS public.cottage
            DROP COLUMN IF EXISTS region_id,
            DROP COLUMN IF EXISTS district_id;
            """,
        ),
    ]
