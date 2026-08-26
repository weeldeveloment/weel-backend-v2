from django.db import migrations


class Migration(migrations.Migration):
    """
    Adds PostGIS geometry(Point, 4326) columns to apartment and cottage tables
    and creates GIST spatial indexes for fast radius-based queries.

    REQUIRES PostGIS to be installed BEFORE running this migration:

        # Ubuntu / Debian
        sudo apt-get install postgresql-<version>-postgis-3
        sudo -u postgres psql -c "CREATE EXTENSION postgis;"

        # Then run:
        python manage.py migrate property 0014

    Once applied, the geographic search in list_apartments / list_cottages
    can be switched to use ST_DWithin instead of the Haversine formula.
    """

    dependencies = [
        ("property", "0013_enable_pg_trgm_search_indexes"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE EXTENSION IF NOT EXISTS postgis;

            -- Add geometry columns (longitude first — GeoJSON convention)
            ALTER TABLE public.apartment
                ADD COLUMN IF NOT EXISTS location geometry(Point, 4326);

            ALTER TABLE public.cottage
                ADD COLUMN IF NOT EXISTS location geometry(Point, 4326);

            -- Populate from existing text lat/lon (skip invalid rows)
            UPDATE public.apartment
            SET location = ST_SetSRID(
                ST_MakePoint(longitude::float, latitude::float), 4326
            )
            WHERE longitude ~ '^-?[0-9]+\\.?[0-9]*$'
              AND latitude  ~ '^-?[0-9]+\\.?[0-9]*$'
              AND longitude != '' AND latitude != '';

            UPDATE public.cottage
            SET location = ST_SetSRID(
                ST_MakePoint(longitude::float, latitude::float), 4326
            )
            WHERE longitude ~ '^-?[0-9]+\\.?[0-9]*$'
              AND latitude  ~ '^-?[0-9]+\\.?[0-9]*$'
              AND longitude != '' AND latitude != '';

            -- GIST spatial indexes
            CREATE INDEX IF NOT EXISTS apt_location_gist_idx
                ON public.apartment USING GIST (location);

            CREATE INDEX IF NOT EXISTS cot_location_gist_idx
                ON public.cottage USING GIST (location);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS apt_location_gist_idx;
            DROP INDEX IF EXISTS cot_location_gist_idx;
            ALTER TABLE public.apartment DROP COLUMN IF EXISTS location;
            ALTER TABLE public.cottage  DROP COLUMN IF EXISTS location;
            """,
        ),
    ]
