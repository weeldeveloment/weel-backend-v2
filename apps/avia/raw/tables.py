AVIA_BOOKING_TABLE = "avia_booking"
AVIA_BOOKING_PASSENGER_TABLE = "avia_booking_passenger"
# Every status Bookhara has told us about, in the order we heard it. Written by
# both the callback endpoint and the polling task, so an order's history
# survives even when a status is passed through on its way to another.
AVIA_BOOKING_EVENT_TABLE = "avia_booking_event"
