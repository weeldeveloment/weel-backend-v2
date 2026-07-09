BEGIN;

DO $$
DECLARE
    s record;
BEGIN
    FOR s IN SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%'
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = s.schema_name
              AND table_name = 'pms_property'
              AND column_name = 'partner_user_id'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.pms_property ADD COLUMN partner_user_id INTEGER',
                s.schema_name
            );
            RAISE NOTICE 'Added partner_user_id to %.pms_property', s.schema_name;
        END IF;
    END LOOP;
END $$;

COMMIT;
