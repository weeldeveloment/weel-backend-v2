BEGIN;

DO $$
DECLARE
    s record;
BEGIN
    FOR s IN SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%'
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = s.schema_name
              AND table_name = 'pms_property'
              AND column_name = 'partner_user_id'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.pms_property DROP COLUMN IF EXISTS partner_user_id',
                s.schema_name
            );
            RAISE NOTICE 'Dropped partner_user_id from %.pms_property', s.schema_name;
        END IF;
    END LOOP;
END $$;

COMMIT;
