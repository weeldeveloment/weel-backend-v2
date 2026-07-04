from django.db import migrations


class Migration(migrations.Migration):
    """
    Enables pg_trgm extension and creates GIN trigram indexes on title and city
    columns for fast fuzzy text search across apartments and cottages.

    pg_trgm must be available in PostgreSQL (it ships with the standard
    postgresql-contrib package). Run:
        sudo apt-get install postgresql-<version>-contrib
    before applying this migration.
    """

    dependencies = [
        ("property", "0012_add_room_columns_to_apartment"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Enable trigram extension (requires postgresql-contrib)
            CREATE EXTENSION IF NOT EXISTS pg_trgm;

            -- GIN trigram indexes on apartment table
            CREATE INDEX CONCURRENTLY IF NOT EXISTS apt_title_trgm_idx
                ON public.apartment USING GIN (title gin_trgm_ops);

            CREATE INDEX CONCURRENTLY IF NOT EXISTS apt_city_trgm_idx
                ON public.apartment USING GIN (city gin_trgm_ops);

            -- GIN trigram indexes on cottage table
            CREATE INDEX CONCURRENTLY IF NOT EXISTS cot_title_trgm_idx
                ON public.cottage USING GIN (title gin_trgm_ops);

            CREATE INDEX CONCURRENTLY IF NOT EXISTS cot_city_trgm_idx
                ON public.cottage USING GIN (city gin_trgm_ops);
            """,
            reverse_sql="""
            DROP INDEX CONCURRENTLY IF EXISTS apt_title_trgm_idx;
            DROP INDEX CONCURRENTLY IF EXISTS apt_city_trgm_idx;
            DROP INDEX CONCURRENTLY IF EXISTS cot_title_trgm_idx;
            DROP INDEX CONCURRENTLY IF EXISTS cot_city_trgm_idx;
            """,
        ),
    ]
