"""Constants from the Hotelios Buyer API."""

from __future__ import annotations


class HotelBookingStatus:
    """`status` on a Hotelios booking, plus the one state that is only ours.

    `DRAFT` never comes back from Hotelios: it is the window between our Create
    call succeeding and the Confirm that actually sends the booking to the
    hotel. Hotelios is explicit that a successful Create does not reserve a
    room, and that the gap should be short — long enough for someone to pay us.
    """

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    WAIT_LIST = "WAIT_LIST"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"

    ALL = frozenset({DRAFT, PENDING, WAIT_LIST, CONFIRMED, CANCELLED})

    #: Held by us but not yet sent to the hotel.
    UNCONFIRMED = frozenset({DRAFT})
    #: Sent to the hotel; still moving.
    LIVE = frozenset({PENDING, WAIT_LIST, CONFIRMED})
    TERMINAL = frozenset({CANCELLED})


class CancellationType:
    REFUNDABLE = "rf"
    NON_REFUNDABLE = "nrf"
    ALL = "all"


class MealPlan:
    ROOM_ONLY = "RO"
    BED_AND_BREAKFAST = "BB"
    HALF_BOARD = "HB"
    FULL_BOARD = "FB"

    CHOICES = frozenset({ROOM_ONLY, BED_AND_BREAKFAST, HALF_BOARD, FULL_BOARD})


class MealOption:
    """The numeric form of the same thing, as `included_meal_options`."""

    BREAKFAST = 0
    LUNCH = 1
    DINNER = 2


class PersonTitle:
    MR = "MR"
    MRS = "MRS"
    CHILD = "CHILD"

    ALL = frozenset({MR, MRS, CHILD})


#: Locales Hotelios returns in every `names`/`address`/`description` block.
LOCALES = ("uz", "ru", "en")

#: The format Hotelios uses for check-in/check-out timestamps in Booking-Flow.
PROVIDER_DATETIME_FORMAT = "%Y/%m/%d %H:%M"
