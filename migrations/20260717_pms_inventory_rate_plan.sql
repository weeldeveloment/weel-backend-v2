-- PMS inventory + rate-plan schema
-- Apply with:
--   psql postgresql://postgres:NG8h7ILfba8lllaSn7J0@95.182.118.156:6000/postgres -f migrations/20260717_pms_inventory_rate_plan.sql

BEGIN;

-- Tenant schemas only: run after SET search_path to the target tenant schema.

CREATE TABLE IF NOT EXISTS pms_room_type (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
    preset VARCHAR(20),
    custom_name VARCHAR(100),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    base_rate NUMERIC(12,2),
    currency VARCHAR(3) DEFAULT 'USD',
    capacity INTEGER DEFAULT 2,
    amenities TEXT[] DEFAULT '{}',
    photos TEXT[] DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pms_rate_plan (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    meal_plan VARCHAR(3) DEFAULT 'RO',
    min_occupancy INTEGER DEFAULT 1,
    max_occupancy INTEGER,
    currency VARCHAR(3) DEFAULT 'USD',
    base_rate NUMERIC(12,2),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(property_id, code)
);

CREATE TABLE IF NOT EXISTS pms_room_type_rate_plan (
    id BIGSERIAL PRIMARY KEY,
    room_type_id BIGINT NOT NULL REFERENCES pms_room_type(id) ON DELETE CASCADE,
    rate_plan_id BIGINT NOT NULL REFERENCES pms_rate_plan(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(room_type_id, rate_plan_id)
);

CREATE TABLE IF NOT EXISTS pms_inventory_block (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
    room_id BIGINT REFERENCES pms_room(id) ON DELETE CASCADE,
    room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE CASCADE,
    rate_plan_id BIGINT REFERENCES pms_rate_plan(id) ON DELETE CASCADE,
    block_type VARCHAR(30) NOT NULL DEFAULT 'blocked',
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    reason TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date > start_date)
);

CREATE TABLE IF NOT EXISTS pms_inventory_restriction (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
    room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE CASCADE,
    rate_plan_id BIGINT REFERENCES pms_rate_plan(id) ON DELETE CASCADE,
    restriction_type VARCHAR(30) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    days_of_week TEXT[] DEFAULT '{}',
    min_los INTEGER,
    max_los INTEGER,
    min_price NUMERIC(12,2),
    max_price NUMERIC(12,2),
    closed_to_arrival BOOLEAN NOT NULL DEFAULT FALSE,
    closed_to_departure BOOLEAN NOT NULL DEFAULT FALSE,
    closed_to_stay BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date >= start_date)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pms_booking_check_dates' AND conrelid = 'pms_booking'::regclass
    ) THEN
        ALTER TABLE pms_booking ADD CONSTRAINT pms_booking_check_dates CHECK (check_out > check_in);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pms_booking_adult_count_check' AND conrelid = 'pms_booking'::regclass
    ) THEN
        ALTER TABLE pms_booking ADD CONSTRAINT pms_booking_adult_count_check CHECK (adult_count >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pms_booking_child_count_check' AND conrelid = 'pms_booking'::regclass
    ) THEN
        ALTER TABLE pms_booking ADD CONSTRAINT pms_booking_child_count_check CHECK (child_count >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pms_rate_plan_property_id ON pms_rate_plan (property_id);
CREATE INDEX IF NOT EXISTS idx_pms_room_type_rate_plan_rate_plan_id ON pms_room_type_rate_plan (rate_plan_id);
CREATE INDEX IF NOT EXISTS idx_pms_inventory_block_property_id ON pms_inventory_block (property_id);
CREATE INDEX IF NOT EXISTS idx_pms_inventory_restriction_property_id ON pms_inventory_restriction (property_id);

COMMIT;
