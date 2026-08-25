# Synced inventory. Primary keys are Hotelios' own ids, not ours — an id in
# these tables means the same thing here as it does in a Booking-Flow payload.
HOTELIOS_COUNTRY_TABLE = "hotelios_country"
HOTELIOS_REGION_TABLE = "hotelios_region"
HOTELIOS_CITY_TABLE = "hotelios_city"
HOTELIOS_HOTEL_TYPE_TABLE = "hotelios_hotel_type"
HOTELIOS_FACILITY_TABLE = "hotelios_facility"
HOTELIOS_EQUIPMENT_TABLE = "hotelios_equipment"
HOTELIOS_NEARBY_PLACE_TYPE_TABLE = "hotelios_nearby_place_type"
HOTELIOS_SERVICE_IN_ROOM_TABLE = "hotelios_service_in_room"
HOTELIOS_BED_TYPE_TABLE = "hotelios_bed_type"
HOTELIOS_STAR_TABLE = "hotelios_star"
HOTELIOS_CURRENCY_TABLE = "hotelios_currency"
HOTELIOS_HOTEL_TABLE = "hotelios_hotel"
HOTELIOS_ROOM_TYPE_TABLE = "hotelios_room_type"
# One row per inventory sync run, so a stalled or half-finished import is
# visible without reading the worker log.
HOTELIOS_SYNC_RUN_TABLE = "hotelios_sync_run"

# Bookings we made through Booking-Flow.
HOTELIOS_BOOKING_TABLE = "hotelios_booking"
HOTELIOS_BOOKING_ROOM_TABLE = "hotelios_booking_room"
HOTELIOS_BOOKING_EVENT_TABLE = "hotelios_booking_event"
