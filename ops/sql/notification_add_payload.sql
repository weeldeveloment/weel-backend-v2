-- Run manually against PostgreSQL (Django ORM migrations are not used for schema in this project).
-- Adds JSON metadata for notifications (e.g. chat conversation_id for marking message pushes read).

ALTER TABLE notification
ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;
