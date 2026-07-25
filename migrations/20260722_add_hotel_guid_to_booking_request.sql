-- create_hotel_booking_request() (apps/b2b/repository.py) inserts hotel_guid,
-- but the column was never added to the table. Add it so B2B hotel booking
-- requests (POST /b2b/hotels/bookings/) stop failing with UndefinedColumn.
-- Apply with psql using ON_ERROR_STOP so deployment fails on any SQL error.

BEGIN;

ALTER TABLE public.b2b_hotel_booking_request
    ADD COLUMN IF NOT EXISTS hotel_guid UUID;

COMMIT;
