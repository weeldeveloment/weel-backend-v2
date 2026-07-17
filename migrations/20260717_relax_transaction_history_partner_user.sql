-- Allow transactions for platform-managed hotels that have no partner owner.
-- Apply with psql using ON_ERROR_STOP so deployment fails on any SQL error.

BEGIN;

ALTER TABLE public.transaction_history
    ALTER COLUMN partner_user_id DROP NOT NULL;

COMMIT;
