from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("property", "0008_add_region_district_to_cottage"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE TABLE IF NOT EXISTS public.cottage_price (
                id bigserial PRIMARY KEY,
                guid uuid NOT NULL UNIQUE,
                created_at timestamp with time zone NOT NULL,
                updated_at timestamp with time zone NOT NULL,
                cottage_id bigint NOT NULL,
                month_from date NOT NULL,
                month_to date NOT NULL,
                price_per_person numeric(12,2),
                price_on_working_days numeric(12,2) NOT NULL,
                price_on_weekends numeric(12,2) NOT NULL,
                CONSTRAINT cottage_price_cottage_id_fkey
                    FOREIGN KEY (cottage_id)
                    REFERENCES public.cottage(id)
                    ON DELETE CASCADE,
                CONSTRAINT cottage_price_month_range_check
                    CHECK (month_to >= month_from)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS cottage_price_cottage_month_from_uniq
                ON public.cottage_price (cottage_id, month_from);

            CREATE INDEX IF NOT EXISTS idx_cottage_price_cottage_id
                ON public.cottage_price (cottage_id);
            """,
            reverse_sql="""
            DROP TABLE IF EXISTS public.cottage_price;
            """,
        ),
    ]
