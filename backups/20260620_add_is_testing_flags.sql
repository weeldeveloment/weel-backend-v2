ALTER TABLE IF EXISTS public.property_apartment
ADD COLUMN IF NOT EXISTS is_testing BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE IF EXISTS public.property_cottage
ADD COLUMN IF NOT EXISTS is_testing BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE IF EXISTS public.apartment
ADD COLUMN IF NOT EXISTS is_testing BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE IF EXISTS public.cottage
ADD COLUMN IF NOT EXISTS is_testing BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
DECLARE
    schema_record RECORD;
BEGIN
    FOR schema_record IN
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name = 'pms_property'
          AND table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
          AND table_schema NOT LIKE 'pg_temp_%'
          AND table_schema NOT LIKE 'pg_toast_temp_%'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.pms_property ADD COLUMN IF NOT EXISTS is_testing BOOLEAN NOT NULL DEFAULT FALSE',
            schema_record.table_schema
        );
    END LOOP;
END $$;
