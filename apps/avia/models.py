"""Constants mirroring the Bookhara reference tables (docs.bookhara.uz/directory).

These are plain string classes rather than Django models: the avia tables are
raw SQL like the rest of this codebase, and the values are Bookhara's, so they
are transcribed rather than invented.
"""

from __future__ import annotations


class AviaBookingStatus:
    """`status` on a Bookhara order."""

    NOT_AVAILABLE = "not_available"
    BOOKED = "booked"
    AWAIT_PAYMENT = "awaitpayment"
    PAID = "paid"
    PARTIALLY_TICKETED = "partiallyticketed"
    TICKETED = "ticketed"
    CANCELLED = "cancelled"
    REFUND_AUTHORIZED = "refundauthorized"
    PARTIALLY_REFUNDED = "partiallyrefunded"
    REFUNDED = "refunded"
    REFUND_REQUEST_SENT = "refund_request_sent"

    ALL = frozenset({
        NOT_AVAILABLE, BOOKED, AWAIT_PAYMENT, PAID, PARTIALLY_TICKETED,
        TICKETED, CANCELLED, REFUND_AUTHORIZED, PARTIALLY_REFUNDED,
        REFUNDED, REFUND_REQUEST_SENT,
    })

    #: Money has been taken but the ticket numbers have not landed yet.
    #: Issuing can take up to ten minutes, so these are the orders worth
    #: re-reading on a schedule.
    AWAITING_TICKETS = frozenset({PAID, PARTIALLY_TICKETED})

    #: Nothing more will happen to an order in one of these states.
    TERMINAL = frozenset({NOT_AVAILABLE, CANCELLED, REFUNDED})

    #: Still holding seats and still cancellable without a refund flow.
    UNPAID = frozenset({BOOKED, AWAIT_PAYMENT})


class PassengerAge:
    """Age groups. The counts requested at search must match those booked."""

    ADULT = "adt"
    CHILD = "chd"
    INFANT = "inf"
    INFANT_WITH_SEAT = "ins"

    ALL = frozenset({ADULT, CHILD, INFANT, INFANT_WITH_SEAT})


class Gender:
    MALE = "M"
    FEMALE = "F"

    ALL = frozenset({MALE, FEMALE})


class ServiceClass:
    ECONOMY = "E"
    BUSINESS = "B"
    ANY = "A"

    ALL = frozenset({ECONOMY, BUSINESS, ANY})


class DocumentType:
    #: Bookhara currently exposes a single universal document type. The offer
    #: search still returns a per-age-group `documents` list, and a booking has
    #: to use a type from it, so this is a default rather than the only option.
    UNIVERSAL = "A"


class FlightType:
    REGULAR = "regular"
    CHARTER = "charter"
    LOWCOST = "lowcost"
    VTRIP = "vtrip"
    SPECIAL = "special"
