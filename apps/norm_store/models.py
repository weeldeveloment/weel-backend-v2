from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace


@dataclass(slots=True)
class _NormBase:
    id: int | None = None
    _meta = SimpleNamespace(db_table="")


class NormCustomer(_NormBase):
    _meta = SimpleNamespace(db_table="norm_customer")


class NormClientSession(_NormBase):
    _meta = SimpleNamespace(db_table="norm_client_session")


class NormClientDevice(_NormBase):
    _meta = SimpleNamespace(db_table="norm_client_device")


class NormPartner(_NormBase):
    _meta = SimpleNamespace(db_table="norm_partner")


class NormPartnerSession(_NormBase):
    _meta = SimpleNamespace(db_table="norm_partner_session")


class NormPartnerDevice(_NormBase):
    _meta = SimpleNamespace(db_table="norm_partner_device")


class NormProperty(_NormBase):
    _meta = SimpleNamespace(db_table="norm_property")


class NormPropertyPrice(_NormBase):
    _meta = SimpleNamespace(db_table="norm_property_price")


class NormBooking(_NormBase):
    _meta = SimpleNamespace(db_table="norm_booking")


class NormBookingStatusHistory(_NormBase):
    _meta = SimpleNamespace(db_table="norm_booking_status_history")


class NormBookingPaymentLink(_NormBase):
    _meta = SimpleNamespace(db_table="norm_booking_payment_link")


class NormExchangeRate(_NormBase):
    _meta = SimpleNamespace(db_table="norm_exchange_rate")


class NormNotification(_NormBase):
    _meta = SimpleNamespace(db_table="norm_notification")


class NormPaymentTransaction(_NormBase):
    _meta = SimpleNamespace(db_table="norm_payment_transaction")
