-- ============================================================================
-- Migration: Add pms_room_type table + room_type_id FK to pms_room
-- For all tenant schemas missing it (32 tenants, excludes tenant_f31a82c68cb3)
-- Run: psql postgresql://postgres:NG8h7ILfba8lllaSn7J0@95.182.118.156:6000/postgres -f migrations/add_pms_room_type.sql
-- ============================================================================

-- tenant_09d06ed79660
SET search_path TO tenant_09d06ed79660, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_10a7d6989e73
SET search_path TO tenant_10a7d6989e73, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_1889667f99b0
SET search_path TO tenant_1889667f99b0, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_2b661e43112c
SET search_path TO tenant_2b661e43112c, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_2bc1f7748fcc
SET search_path TO tenant_2bc1f7748fcc, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_35ca57f46791
SET search_path TO tenant_35ca57f46791, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_3d1dbad8727e
SET search_path TO tenant_3d1dbad8727e, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_3e8217af8929
SET search_path TO tenant_3e8217af8929, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_404b665d521a
SET search_path TO tenant_404b665d521a, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_414abc53127f
SET search_path TO tenant_414abc53127f, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_6088c86737cc
SET search_path TO tenant_6088c86737cc, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_626ce71ce30b
SET search_path TO tenant_626ce71ce30b, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_7812eb60d4ee
SET search_path TO tenant_7812eb60d4ee, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_7a36f4a2a17f
SET search_path TO tenant_7a36f4a2a17f, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_7b0aa82c0731
SET search_path TO tenant_7b0aa82c0731, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_95518e1b58f6
SET search_path TO tenant_95518e1b58f6, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_9665d1ead84d
SET search_path TO tenant_9665d1ead84d, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_ac9adcab78d6
SET search_path TO tenant_ac9adcab78d6, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_aee8916609c8
SET search_path TO tenant_aee8916609c8, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_b50bbf80f779
SET search_path TO tenant_b50bbf80f779, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_b6cd7f4f267e
SET search_path TO tenant_b6cd7f4f267e, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_b8d47ff23357
SET search_path TO tenant_b8d47ff23357, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_beee63434a06
SET search_path TO tenant_beee63434a06, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_c9d468e3352e
SET search_path TO tenant_c9d468e3352e, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_daacd93ef8bb
SET search_path TO tenant_daacd93ef8bb, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_db0a42dbb676
SET search_path TO tenant_db0a42dbb676, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_demo
SET search_path TO tenant_demo, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_e3d392e3b246
SET search_path TO tenant_e3d392e3b246, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_e6878c15ddb7
SET search_path TO tenant_e6878c15ddb7, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_f9dc7a1af61a
SET search_path TO tenant_f9dc7a1af61a, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_fca6cfaf0173
SET search_path TO tenant_fca6cfaf0173, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- tenant_fd2613cc82c5
SET search_path TO tenant_fd2613cc82c5, public;
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
ALTER TABLE pms_room ADD COLUMN IF NOT EXISTS room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL;

-- Reset search_path
SET search_path TO public;
