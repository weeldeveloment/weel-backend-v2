"""
Write-through sync from Django models to norm_* tables when USE_NORM_DATASTORE is on.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

logger = logging.getLogger(__name__)


def norm_enabled() -> bool:
    return bool(getattr(settings, "USE_NORM_DATASTORE", False))


def ensure_norm_customer(client):
    if not norm_enabled():
        return None
    from norm_store.models import NormCustomer
    from users.models.clients import Client

    if not isinstance(client, Client):
        return None
    try:
        nc, _ = NormCustomer.objects.update_or_create(
            legacy_client_id=client.id,
            defaults={
                "legacy_client_guid": client.guid,
                "first_name": client.first_name or "",
                "last_name": client.last_name or "",
                "phone_number": client.phone_number or "",
                "is_active": bool(client.is_active),
            },
        )
        return nc
    except DatabaseError as e:
        logger.exception("norm_store.ensure_norm_customer: %s", e)
        return None


def ensure_norm_partner(partner):
    if not norm_enabled():
        return None
    from norm_store.models import NormPartner
    from users.models.partners import Partner

    if not isinstance(partner, Partner):
        return None
    try:
        np, _ = NormPartner.objects.update_or_create(
            legacy_partner_id=partner.id,
            defaults={
                "legacy_partner_guid": partner.guid,
                "first_name": partner.first_name or "",
                "last_name": partner.last_name or "",
                "username": partner.username or "",
                "phone_number": partner.phone_number or "",
                "email": partner.email or "",
                "is_active": bool(partner.is_active),
                "is_verified": partner.is_verified,
                "verified_by_admin_id": getattr(partner.verified_by, "id", None)
                if getattr(partner, "verified_by_id", None)
                else None,
                "verified_at": partner.verified_at,
            },
        )
        return np
    except DatabaseError as e:
        logger.exception("norm_store.ensure_norm_partner: %s", e)
        return None


def sync_property_to_norm(prop) -> None:
    if not norm_enabled():
        return
    from norm_store.models import NormProperty, NormPropertyPrice

    try:
        npartner = ensure_norm_partner(prop.partner)
        loc = getattr(prop, "property_location", None)
        defaults = {
            "legacy_property_guid": prop.guid,
            "title": (prop.title or "")[:75],
            "currency": str(prop.currency or "USD")[:3],
            "verification_status": str(prop.verification_status or "")[:10],
            "is_verified": bool(prop.is_verified),
            "is_archived": bool(prop.is_archived),
            "region_id": prop.region_id,
            "district_id": prop.district_id,
            "city": (loc.city if loc else "")[:100],
            "country": (loc.country if loc else "")[:100],
            "latitude": loc.latitude if loc else None,
            "longitude": loc.longitude if loc else None,
            "partner_id": npartner.id if npartner else None,
        }
        norm_p, _ = NormProperty.objects.update_or_create(
            legacy_property_id=prop.id,
            defaults=defaults,
        )
        for pp in prop.property_price.all():
            NormPropertyPrice.objects.update_or_create(
                legacy_property_price_id=pp.id,
                defaults={
                    "property": norm_p,
                    "month_from": pp.month_from,
                    "month_to": pp.month_to,
                    "price_per_person": pp.price_per_person,
                    "price_on_working_days": pp.price_on_working_days,
                    "price_on_weekends": pp.price_on_weekends,
                },
            )
    except DatabaseError as e:
        logger.exception("norm_store.sync_property_to_norm: %s", e)


def sync_booking_to_norm(booking, old_status: str | None) -> None:
    if not norm_enabled():
        return
    from booking.models import Booking
    from norm_store.models import NormBooking, NormBookingStatusHistory, NormProperty

    if not isinstance(booking, Booking):
        return
    try:
        nc = ensure_norm_customer(booking.client)
        np = NormProperty.objects.filter(legacy_property_id=booking.property_id).first()
        if not nc or not np:
            sync_property_to_norm(booking.property)
            np = NormProperty.objects.filter(legacy_property_id=booking.property_id).first()
        if not nc or not np:
            logger.warning(
                "norm_store.sync_booking_to_norm: missing norm customer or property booking=%s",
                booking.guid,
            )
            return

        nb, _ = NormBooking.objects.update_or_create(
            legacy_booking_guid=booking.guid,
            defaults={
                "legacy_booking_id": booking.id,
                "booking_number": booking.booking_number,
                "check_in": booking.check_in,
                "check_out": booking.check_out,
                "adults": booking.adults,
                "children": booking.children,
                "babies": booking.babies,
                "current_status": booking.status,
                "cancellation_reason": booking.cancellation_reason or "",
                "confirmed_at": booking.confirmed_at,
                "cancelled_at": booking.cancelled_at,
                "completed_at": booking.completed_at,
                "reminder_sent": bool(booking.reminder_sent),
                "payment_reminder_stage": booking.payment_reminder_stage or "",
                "customer_id": nc.id,
                "property_id": np.id,
            },
        )
        if old_status is not None and old_status != booking.status:
            NormBookingStatusHistory.objects.create(
                booking=nb,
                from_status=old_status or "",
                to_status=booking.status,
                reason=booking.cancellation_reason or "",
                source="api",
                changed_at=timezone.now(),
            )
    except DatabaseError as e:
        logger.exception("norm_store.sync_booking_to_norm: %s", e)


def sync_plum_to_norm(tx) -> None:
    if not norm_enabled():
        return
    from norm_store.models import NormPaymentTransaction
    from payment.models import PlumTransaction

    if not isinstance(tx, PlumTransaction):
        return
    try:
        NormPaymentTransaction.objects.update_or_create(
            legacy_plum_transaction_id=tx.id,
            defaults={
                "legacy_plum_transaction_guid": tx.guid,
                "provider_transaction_id": tx.transaction_id,
                "provider_hold_id": tx.hold_id,
                "amount": tx.amount,
                "type": tx.type,
                "status": tx.status,
                "card_id": tx.card_id or "",
                "extra_id": tx.extra_id or "",
            },
        )
    except DatabaseError as e:
        logger.exception("norm_store.sync_plum_to_norm: %s", e)
