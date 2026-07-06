-- =============================================================================
-- Rollback: Query Optimization Indexes (20260706)
-- Run this to revert all indexes created by 20260706_query_optimization_indexes.sql
-- WARNING: Only run if indexes cause issues; otherwise they are beneficial.
-- =============================================================================

BEGIN;

-- Public schema indexes
DROP INDEX IF EXISTS public.idx_cottage_price_cottage_date_range;
DROP INDEX IF EXISTS public.idx_review_apartment_id;
DROP INDEX IF EXISTS public.idx_review_cottage_id;
DROP INDEX IF EXISTS public.idx_review_user_id;
DROP INDEX IF EXISTS public.idx_booking_client_user_id;
DROP INDEX IF EXISTS public.idx_chat_message_conversation_id;
DROP INDEX IF EXISTS public.idx_chat_message_sender_user_id;
DROP INDEX IF EXISTS public.idx_transaction_history_booking_id;
DROP INDEX IF EXISTS public.idx_transaction_history_client_user_id;
DROP INDEX IF EXISTS public.idx_transaction_history_partner_user_id;
DROP INDEX IF EXISTS public.idx_property_image_property_id;
DROP INDEX IF EXISTS public.idx_notification_recipient_user_id;
DROP INDEX IF EXISTS public.idx_calendars_booking_id;
DROP INDEX IF EXISTS public.idx_story_media_story_id;
DROP INDEX IF EXISTS public.idx_user_map_user_id;

-- Restore redundant indexes that were dropped
CREATE INDEX IF NOT EXISTS ix_b2b_budget_request_trip_id       ON public.b2b_budget_request (trip_id);
CREATE INDEX IF NOT EXISTS ix_b2b_budget_request_employee_id   ON public.b2b_budget_request (employee_id);
CREATE INDEX IF NOT EXISTS ix_b2b_business_trip_company_id     ON public.b2b_business_trip (company_id);
CREATE INDEX IF NOT EXISTS ix_b2b_trip_employee_trip_id        ON public.b2b_trip_employee (trip_id);
CREATE INDEX IF NOT EXISTS ix_b2b_trip_employee_employee_id    ON public.b2b_trip_employee (employee_id);
CREATE INDEX IF NOT EXISTS idx_cottage_price_cottage_id        ON public.cottage_price (cottage_id);
CREATE INDEX IF NOT EXISTS district_region_idx                 ON public.district (region_id);
CREATE INDEX IF NOT EXISTS ix_favorite_user_id                 ON public.favorite (user_id);
CREATE INDEX IF NOT EXISTS ix_platform_favorites_user_id       ON public.platform_favorites (user_id);
CREATE INDEX IF NOT EXISTS idx_rec_graph_client                ON public.recommendation_graph (client_id);

-- Tenant schema indexes
DO $$
DECLARE
    s record;
BEGIN
    FOR s IN SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%'
    LOOP
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_property_image_property_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_room_property_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_room_room_type_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_room_image_room_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_booking_property_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_booking_room_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_booking_guest_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_booking_created_by', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_booking_history_booking_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_rate_property_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_rate_room_type_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_review_property_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_review_booking_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_room_type_property_id', s.schema_name);
        EXECUTE format('DROP INDEX IF EXISTS %I.idx_pms_calendar_slot_status_expires', s.schema_name);
    END LOOP;
END $$;

COMMIT;
