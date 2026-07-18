DO $$
DECLARE
    tenant_schema text;
    has_property_currency boolean;
    has_room_type boolean;
    has_room_type_currency boolean;
BEGIN
    FOR tenant_schema IN
        SELECT s.schema_name
        FROM information_schema.schemata s
        WHERE s.schema_name LIKE 'tenant\_%' ESCAPE '\'
        ORDER BY s.schema_name
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = tenant_schema AND table_name = 'pms_property'
        ) OR NOT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = tenant_schema AND table_name = 'pms_room'
        ) THEN
            CONTINUE;
        END IF;

        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = tenant_schema
              AND table_name = 'pms_property'
              AND column_name = 'currency'
        ) INTO has_property_currency;

        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = tenant_schema
              AND table_name = 'pms_room_type'
        ) INTO has_room_type;

        SELECT has_room_type AND EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = tenant_schema
              AND table_name = 'pms_room_type'
              AND column_name = 'currency'
        ) INTO has_room_type_currency;

        EXECUTE format('ALTER TABLE %I.pms_room ADD COLUMN IF NOT EXISTS currency varchar(3)', tenant_schema);

        EXECUTE format($sql$
            CREATE TABLE IF NOT EXISTS %I.pms_currency_archive (
                id bigserial PRIMARY KEY,
                source_table varchar(64) NOT NULL,
                source_id bigint NOT NULL,
                property_id bigint,
                currency varchar(3),
                archived_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (source_table, source_id)
            )
        $sql$, tenant_schema);

        IF has_property_currency THEN
            EXECUTE format($sql$
                INSERT INTO %I.pms_currency_archive (source_table, source_id, property_id, currency)
                SELECT 'pms_property', id, id, currency
                FROM %I.pms_property
                WHERE currency IS NOT NULL
                ON CONFLICT (source_table, source_id) DO UPDATE
                SET property_id = EXCLUDED.property_id,
                    currency = EXCLUDED.currency,
                    archived_at = now()
            $sql$, tenant_schema, tenant_schema);
        END IF;

        IF has_room_type_currency THEN
            EXECUTE format($sql$
                INSERT INTO %I.pms_currency_archive (source_table, source_id, property_id, currency)
                SELECT 'pms_room_type', id, property_id, currency
                FROM %I.pms_room_type
                WHERE currency IS NOT NULL
                ON CONFLICT (source_table, source_id) DO UPDATE
                SET property_id = EXCLUDED.property_id,
                    currency = EXCLUDED.currency,
                    archived_at = now()
            $sql$, tenant_schema, tenant_schema);

            EXECUTE format($sql$
                UPDATE %I.pms_room r
                SET currency = COALESCE(r.currency, rt.currency)
                FROM %I.pms_room_type rt
                WHERE r.room_type_id = rt.id
                  AND r.currency IS NULL
            $sql$, tenant_schema, tenant_schema);
        END IF;

        IF has_property_currency THEN
            EXECUTE format($sql$
                UPDATE %I.pms_room r
                SET currency = COALESCE(r.currency, p.currency)
                FROM %I.pms_property p
                WHERE r.property_id = p.id
                  AND r.currency IS NULL
            $sql$, tenant_schema, tenant_schema);
        END IF;

        EXECUTE format('UPDATE %I.pms_room SET currency = ''UZS'' WHERE currency IS NULL', tenant_schema);
        EXECUTE format('ALTER TABLE %I.pms_room ALTER COLUMN currency SET DEFAULT ''UZS''', tenant_schema);
        EXECUTE format('ALTER TABLE %I.pms_room ALTER COLUMN currency SET NOT NULL', tenant_schema);

        IF has_property_currency THEN
            EXECUTE format('ALTER TABLE %I.pms_property DROP COLUMN currency', tenant_schema);
        END IF;

        IF has_room_type_currency THEN
            EXECUTE format('ALTER TABLE %I.pms_room_type DROP COLUMN currency', tenant_schema);
        END IF;
    END LOOP;
END $$;
