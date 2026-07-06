-- =============================================================================
-- Query Optimization Indexes
-- Created: 2026-07-06
-- Applied: already live via psql; idempotent for re-runs
-- =============================================================================

BEGIN;

-- 1. cottage_price: composite index for CURRENT_DATE BETWEEN month_from AND month_to
CREATE INDEX IF NOT EXISTS idx_cottage_price_cottage_date_range
    ON public.cottage_price (cottage_id, month_from, month_to);

-- 2. review: FK indexes (eliminates 479K seq scans)
CREATE INDEX IF NOT EXISTS idx_review_apartment_id ON public.review (apartment_id);
CREATE INDEX IF NOT EXISTS idx_review_cottage_id   ON public.review (cottage_id);
CREATE INDEX IF NOT EXISTS idx_review_user_id      ON public.review (user_id);

-- 3. booking: client lookup and property reference
CREATE INDEX IF NOT EXISTS idx_booking_client_user_id ON public.booking (client_user_id);
-- idx_booking_property_id already exists, skip

-- 4. chat_message: conversation listing and sender lookups
CREATE INDEX IF NOT EXISTS idx_chat_message_conversation_id ON public.chat_message (conversation_id);
CREATE INDEX IF NOT EXISTS idx_chat_message_sender_user_id   ON public.chat_message (sender_user_id);

-- 5. transaction_history: payment/tx lookups
CREATE INDEX IF NOT EXISTS idx_transaction_history_booking_id       ON public.transaction_history (booking_id);
CREATE INDEX IF NOT EXISTS idx_transaction_history_client_user_id   ON public.transaction_history (client_user_id);
CREATE INDEX IF NOT EXISTS idx_transaction_history_partner_user_id  ON public.transaction_history (partner_user_id);

-- 6. property_image: image loading by property
CREATE INDEX IF NOT EXISTS idx_property_image_property_id ON public.property_image (property_id);

-- 7. notification: user notifications
CREATE INDEX IF NOT EXISTS idx_notification_recipient_user_id ON public.notification (recipient_user_id);

-- 8. calendars: booking lookup
CREATE INDEX IF NOT EXISTS idx_calendars_booking_id ON public.calendars (booking_id);

-- 9. story_media: stories media loading
CREATE INDEX IF NOT EXISTS idx_story_media_story_id ON public.story_media (story_id);

-- 10. user_map: legacy ID mapping lookup
CREATE INDEX IF NOT EXISTS idx_user_map_user_id ON public.user_map (user_id);

-- 11. Drop 10 redundant indexes (covered by composite indexes with same leading column)
DROP INDEX IF EXISTS public.ix_b2b_budget_request_trip_id;
DROP INDEX IF EXISTS public.ix_b2b_budget_request_employee_id;
DROP INDEX IF EXISTS public.ix_b2b_business_trip_company_id;
DROP INDEX IF EXISTS public.ix_b2b_trip_employee_trip_id;
DROP INDEX IF EXISTS public.ix_b2b_trip_employee_employee_id;
DROP INDEX IF EXISTS public.idx_cottage_price_cottage_id;
DROP INDEX IF EXISTS public.district_region_idx;
DROP INDEX IF EXISTS public.ix_favorite_user_id;
DROP INDEX IF EXISTS public.ix_platform_favorites_user_id;
DROP INDEX IF EXISTS public.idx_rec_graph_client;

-- 12. Add FK indexes to ALL existing tenant schemas (33 tenants)
DO $$
DECLARE
    s record;
BEGIN
    FOR s IN SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%'
    LOOP
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_property_image_property_id ON %I.pms_property_image (property_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_room_property_id ON %I.pms_room (property_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_room_room_type_id ON %I.pms_room (room_type_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_room_image_room_id ON %I.pms_room_image (room_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_booking_property_id ON %I.pms_booking (property_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_booking_room_id ON %I.pms_booking (room_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_booking_guest_id ON %I.pms_booking (guest_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_booking_created_by ON %I.pms_booking (created_by)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_booking_history_booking_id ON %I.pms_booking_history (booking_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_rate_property_id ON %I.pms_rate (property_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_rate_room_type_id ON %I.pms_rate (room_type_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_review_property_id ON %I.pms_review (property_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_review_booking_id ON %I.pms_review (booking_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_room_type_property_id ON %I.pms_room_type (property_id)', s.schema_name);
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_pms_calendar_slot_status_expires ON %I.pms_calendar_slot (status, hold_expires_at)', s.schema_name);
    END LOOP;
END $$;

-- 13. Refresh stale statistics
ANALYZE public.district;
ANALYZE public.prefecture;
ANALYZE public.region;
ANALYZE public.review;
ANALYZE public.booking;
ANALYZE public.calendar;
ANALYZE public.cottage_price;

COMMIT;
