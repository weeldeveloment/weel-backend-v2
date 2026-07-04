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
            'ALTER TABLE %I.pms_property ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE',
            schema_record.table_schema
        );
        EXECUTE format(
            'ALTER TABLE %I.pms_property ADD COLUMN IF NOT EXISTS verification_status VARCHAR(10) NOT NULL DEFAULT ''waiting''',
            schema_record.table_schema
        );
    END LOOP;
END $$;
