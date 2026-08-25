from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema

from apps.avia import raw_repository as repo
from apps.avia import service
from apps.avia.client import (
    BookharaError,
    BookharaExpiredError,
    BookharaUnconfirmedError,
    get_client,
)
from apps.avia.models import AviaBookingStatus
from apps.avia.raw_serializers import (
    AviaBookingSerializer,
    B2BCreateBookingSerializer,
    CreateBookingSerializer,
    OfferSearchSerializer,
    ScheduleQuerySerializer,
)

logger = logging.getLogger(__name__)

# Bookhara validation failures. Its own message names the offending fields, so
# it is passed through as a 400 rather than being flattened into a gateway error.
BOOKHARA_VALIDATION_ERROR_CODE = 8
# Not enough money on the Bookhara deposit to issue this ticket. Operational,
# not a client mistake, but the caller needs to see it distinctly.
BOOKHARA_INSUFFICIENT_DEPOSIT_ERROR_CODE = 1048


def _language(request) -> str | None:
    """Pass the caller's language through to Bookhara.

    Bookhara localises airport, city and carrier names from `Accept-Language`
    and supports `en` and `ru`. Requests arriving as `uz` get `en`, which reads
    better in an Uzbek UI than Russian transliterations of airport names.
    """
    header = (request.headers.get("Accept-Language") or "").strip().lower()
    if not header:
        return None
    primary = header.split(",")[0].split("-")[0].strip()
    return primary if primary in ("en", "ru") else "en"


def _error_response(exc: BookharaError) -> Response:
    """Translate a provider failure into something the apps can act on."""
    body = {
        "detail": exc.message,
        "error_code": exc.error_code,
        "request_id": exc.request_id,
    }
    if exc.errors:
        body["errors"] = exc.errors

    if isinstance(exc, BookharaExpiredError):
        body["detail"] = (
            exc.message or "This offer or booking has expired. Please search again."
        )
        body["expired"] = True
        return Response(body, status=status.HTTP_404_NOT_FOUND)

    if isinstance(exc, BookharaUnconfirmedError):
        # The carrier has not answered yet. The documented remedy is to repeat
        # the same call, so say so rather than presenting it as a dead end.
        body["retryable"] = True
        return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    if isinstance(exc, service.PriceChangedError) or exc.is_price_changed:
        body["price_changed"] = True
        if exc.data:
            body["price"] = exc.data
        return Response(body, status=status.HTTP_409_CONFLICT)

    if exc.is_duplicate_booking:
        body["duplicate"] = True
        body["existing_booking_id"] = exc.existing_booking_id
        return Response(body, status=status.HTTP_409_CONFLICT)

    if exc.error_code == BOOKHARA_VALIDATION_ERROR_CODE:
        return Response(body, status=status.HTTP_400_BAD_REQUEST)

    if exc.error_code == BOOKHARA_INSUFFICIENT_DEPOSIT_ERROR_CODE:
        logger.error("avia: Bookhara deposit exhausted (request_id=%s)", exc.request_id)
        body["detail"] = "Ticketing is temporarily unavailable. Please try again later."
        return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    logger.warning("avia: Bookhara call failed — %s", exc)
    return Response(body, status=status.HTTP_502_BAD_GATEWAY)


class BookharaAPIView(APIView):
    """Turns any `BookharaError` raised in a handler into a response.

    Every endpoint here is a call to a third party that can refuse for a dozen
    documented reasons, and each of them means something specific to the app.
    Handling that in one place keeps the handlers about the flow.
    """

    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, BookharaError):
            return _error_response(exc)
        return super().handle_exception(exc)


# ---------------------------------------------------------------------------
# Caller scoping
#
# Both the consumer apps and the B2B dashboard reach these endpoints with their
# own token type. Search is the same for everyone; a booking belongs either to
# a client user or to a company, and must only ever be visible to its owner.
# ---------------------------------------------------------------------------

def _ownership(request) -> dict[str, int | None]:
    user = request.user
    company_id = user.get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    if company_id:
        return {"b2b_company_id": company_id, "b2b_user_id": user_id}
    return {"client_user_id": user_id}


def _visible_booking(request, guid: str) -> dict | None:
    booking = repo.fetch_booking_by_guid(guid)
    if booking is None:
        return None
    owner = _ownership(request)
    if "b2b_company_id" in owner:
        if booking["b2b_company_id"] != owner["b2b_company_id"]:
            return None
    elif booking["client_user_id"] != owner["client_user_id"]:
        return None
    return booking


def _with_passengers(booking: dict) -> dict:
    return {**booking, "passengers": repo.fetch_passengers(booking["id"])}


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------

class AviaOfferSearchView(BookharaAPIView):
    """POST /api/avia/offers/search — priced itineraries for a route."""

    @swagger_auto_schema(request_body=OfferSearchSerializer)
    def post(self, request):
        serializer = OfferSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        language = _language(request)
        offers = get_client(language=language).search_offers(
            **serializer.to_provider_params(), language=language
        )
        return Response({"count": len(offers), "results": offers})


class AviaOfferDetailView(BookharaAPIView):
    """GET /api/avia/offers/{offer_id} — re-check seats and price.

    Offers live for hours, not days. A 404 here means the offer aged out and
    the caller has to search again — which is what the response says.
    """

    def get(self, request, offer_id: str):
        with_services = request.query_params.get("with_additional_services") in ("1", "true", "True")
        language = _language(request)
        offer = get_client(language=language).get_offer(
            offer_id, with_additional_services=with_services, language=language
        )
        return Response(offer)


class AviaOfferFareFamilyView(BookharaAPIView):
    """GET /api/avia/offers/{offer_id}/fare-family — the upsell ladder."""

    def get(self, request, offer_id: str):
        language = _language(request)
        families = get_client(language=language).get_fare_family(offer_id, language=language)
        return Response({"count": len(families), "results": families})


class AviaOfferRulesView(BookharaAPIView):
    """GET /api/avia/offers/{offer_id}/rules — fare conditions per direction."""

    def get(self, request, offer_id: str):
        language = _language(request)
        rules = get_client(language=language).get_offer_rules(offer_id, language=language)
        return Response({"results": rules})


class AviaScheduleView(BookharaAPIView):
    """GET /api/avia/schedule — published flights, without prices."""

    @swagger_auto_schema(query_serializer=ScheduleQuerySerializer)
    def get(self, request):
        serializer = ScheduleQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        language = _language(request)
        flights = get_client(language=language).get_schedule(
            departure_from=data["departure_from"].isoformat(),
            departure_to=data["departure_to"].isoformat(),
            airport_from=(data.get("airport_from") or "").upper() or None,
            airport_to=(data.get("airport_to") or "").upper() or None,
            airlines=[a.upper() for a in data.get("airlines") or []] or None,
            language=language,
        )
        return Response({"count": len(flights), "results": flights})


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

class AviaBookingListCreateView(BookharaAPIView):
    """GET/POST /api/avia/bookings/ — this caller's orders, and new ones."""

    @swagger_auto_schema(responses={200: AviaBookingSerializer(many=True)})
    def get(self, request):
        owner = _ownership(request)
        if "b2b_company_id" in owner:
            bookings = repo.fetch_bookings_for_company(
                b2b_company_id=owner["b2b_company_id"],
                trip_id=request.query_params.get("trip_id") or None,
                status=request.query_params.get("status") or None,
            )
        else:
            bookings = repo.fetch_bookings_for_client(client_user_id=owner["client_user_id"])
        return Response(AviaBookingSerializer(bookings, many=True).data)

    @swagger_auto_schema(
        request_body=B2BCreateBookingSerializer,
        responses={201: AviaBookingSerializer()},
    )
    def post(self, request):
        offer_id = request.data.get("offer_id")
        if not offer_id:
            return Response(
                {"detail": "offer_id is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        owner = _ownership(request)
        is_corporate = "b2b_company_id" in owner
        serializer_class = B2BCreateBookingSerializer if is_corporate else CreateBookingSerializer
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if is_corporate:
            owner = {
                **owner,
                "b2b_trip_id": data.get("trip_id"),
                "b2b_employee_id": data.get("employee_id"),
            }

        language = _language(request)
        booking = service.create_booking(
            offer_id=offer_id,
            payer_name=data["payer_name"],
            payer_email=data["payer_email"],
            payer_tel=data["payer_tel"],
            passengers=serializer.provider_passengers(),
            order_note=data.get("order_note") or None,
            additional_services=data.get("additional_services") or None,
            language=language,
            **owner,
        )
        return Response(
            AviaBookingSerializer(_with_passengers(booking)).data,
            status=status.HTTP_201_CREATED,
        )


class AviaBookingDetailView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/ — the local copy, optionally refreshed."""

    @swagger_auto_schema(responses={200: AviaBookingSerializer()})
    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.query_params.get("refresh") in ("1", "true", "True"):
            booking = service.refresh_booking(
                booking, language=_language(request), source="api"
            )
        return Response(AviaBookingSerializer(_with_passengers(booking)).data)


class AviaBookingRefreshView(BookharaAPIView):
    """POST /api/avia/bookings/{guid}/refresh/ — re-read from Bookhara."""

    def post(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        booking = service.refresh_booking(booking, language=_language(request), source="api")
        return Response(AviaBookingSerializer(_with_passengers(booking)).data)


class AviaBookingRulesView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/rules/ — fare conditions after booking."""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        language = _language(request)
        rules = get_client(language=language).get_booking_rules(
            booking["provider_booking_id"], language=language
        )
        return Response({"results": rules})


class AviaBookingPriceCheckView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/check-price/ — has the fare moved?"""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(get_client().check_booking_price(booking["provider_booking_id"]))


class AviaBookingPaymentView(BookharaAPIView):
    """POST /api/avia/bookings/{guid}/payment/ — charge the deposit and ticket.

    This is the point of no return: it moves real money and hands the order to
    the carrier for issuing. The price is re-checked first, and a change stops
    the payment with a 409 so a person can agree to the new amount.
    """

    @swagger_auto_schema(responses={200: AviaBookingSerializer()})
    def post(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        if booking["status"] not in AviaBookingStatus.UNPAID:
            return Response(
                {
                    "detail": f"A booking in status '{booking['status']}' cannot be paid for.",
                    "status": booking["status"],
                },
                status=status.HTTP_409_CONFLICT,
            )
        booking = service.pay_booking(booking, language=_language(request))
        return Response(AviaBookingSerializer(_with_passengers(booking)).data)


class AviaBookingPaymentPermissionView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/payment-permission/."""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(get_client().payment_permission(booking["provider_booking_id"]))


class AviaBookingFiscalizationView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/fiscalization/ — receipt data for the OFD."""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(service.fetch_fiscalization(booking))


class AviaBookingReceiptView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/receipt/ — itinerary PDFs, per passenger."""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        if booking["status"] != AviaBookingStatus.TICKETED:
            return Response(
                {
                    "detail": "Itinerary receipts exist only once the tickets are issued.",
                    "status": booking["status"],
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"results": service.fetch_receipts(booking)})


class AviaBookingRefundAmountView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/refund-amount/ — refund minus penalty."""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(get_client().get_refund_amounts(booking["provider_booking_id"]))


class AviaBookingCancelView(BookharaAPIView):
    """DELETE /api/avia/bookings/{guid}/ — cancel or refund, whichever applies.

    Which call to make depends on where the order is, and getting it wrong
    either fails or costs a penalty that did not have to be paid:

      * unpaid          → cancel-unpaid, free
      * paid / ticketed → void, a full refund with no penalty when the fare
                          allows it, otherwise auto-cancel with the penalty
      * neither         → manual-refund, which raises it with Bookhara's
                          call centre

    `mode` forces one of them; by default the cheapest applicable one is used.
    """

    @swagger_auto_schema(responses={200: AviaBookingSerializer()})
    def delete(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        mode = (request.query_params.get("mode") or "auto").lower()
        if mode not in ("auto", "cancel_unpaid", "void", "auto_cancel", "manual_refund"):
            return Response(
                {"detail": f"Unknown cancellation mode '{mode}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        language = _language(request)
        if mode != "auto":
            booking = getattr(service, mode)(booking, language=language)
            return Response(AviaBookingSerializer(_with_passengers(booking)).data)

        if booking["status"] in AviaBookingStatus.UNPAID:
            booking = service.cancel_unpaid(booking, language=language)
            return Response(AviaBookingSerializer(_with_passengers(booking)).data)

        if booking["status"] not in (
            AviaBookingStatus.PAID,
            AviaBookingStatus.TICKETED,
            AviaBookingStatus.PARTIALLY_TICKETED,
        ):
            return Response(
                {
                    "detail": f"A booking in status '{booking['status']}' cannot be cancelled.",
                    "status": booking["status"],
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Try the free path first. Bookhara documents that void can be refused
        # even when the fare rules permit it, so a refusal falls through to the
        # penalised refund rather than being reported as a failure.
        try:
            booking = service.void(booking, language=language)
            return Response(AviaBookingSerializer(_with_passengers(booking)).data)
        except BookharaError as void_error:
            logger.info(
                "avia: void unavailable for %s (%s); trying auto-cancel.",
                booking["provider_booking_id"],
                void_error,
            )

        try:
            booking = service.auto_cancel(booking, language=language)
        except BookharaError as auto_error:
            logger.info(
                "avia: auto-cancel unavailable for %s (%s); raising a manual refund.",
                booking["provider_booking_id"],
                auto_error,
            )
            booking = service.manual_refund(booking, language=language)

        return Response(AviaBookingSerializer(_with_passengers(booking)).data)

    def post(self, request, guid: str):
        """Same operation for clients that cannot issue a DELETE."""
        return self.delete(request, guid)


class AviaBookingEventsView(BookharaAPIView):
    """GET /api/avia/bookings/{guid}/events/ — the status history we recorded."""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        events = repo.fetch_status_events(booking_id=booking["id"])
        return Response({
            "results": [
                {
                    "status": e["status"],
                    "previous_status": e["previous_status"],
                    "source": e["source"],
                    "created_at": e["created_at"],
                }
                for e in events
            ]
        })


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

class AviaBalanceView(BookharaAPIView):
    """GET /api/avia/balance/ — the deposit ticketing draws on.

    Staff-facing: an exhausted deposit stops every payment, so this needs to be
    visible before the first customer discovers it.
    """

    def get(self, request):
        return Response(get_client().check_balance())


class AviaStatusCallbackView(APIView):
    """POST /api/avia/callback/status/ — Bookhara telling us an order moved.

    The endpoint is unauthenticated in the usual sense — Bookhara has no token
    of ours — so the `X-Auth` header is the whole of the authentication, and it
    is checked before the body is looked at.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def _expected_token(self, provider_booking_id: str) -> str | None:
        modifier = settings.BOOKHARA_CALLBACK_SECRET
        if not (modifier and settings.BOOKHARA_EMAIL):
            return None
        raw = f"{settings.BOOKHARA_EMAIL}{provider_booking_id}{modifier}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return base64.b64encode(digest.encode("utf-8")).decode("ascii")

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        # Bookhara posts the same body the booking-details endpoint returns,
        # which may or may not be wrapped in the standard envelope.
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        provider_booking_id = data.get("id")
        if not provider_booking_id:
            return Response(
                {"detail": "Missing booking id."}, status=status.HTTP_400_BAD_REQUEST
            )

        expected = self._expected_token(provider_booking_id)
        if expected is None:
            logger.error(
                "avia: status callback received but BOOKHARA_CALLBACK_SECRET is not set."
            )
            return Response(
                {"detail": "Callbacks are not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        supplied = request.headers.get("X-Auth") or ""
        if not hmac.compare_digest(supplied, expected):
            logger.warning(
                "avia: rejected status callback for %s — bad X-Auth token.",
                provider_booking_id,
            )
            return Response({"detail": "Invalid token."}, status=status.HTTP_401_UNAUTHORIZED)

        existing = repo.fetch_booking_by_provider_id(provider_booking_id)
        if existing is None:
            # An order we never created — nothing to update, and nothing worth
            # failing the callback over.
            logger.warning("avia: status callback for unknown booking %s.", provider_booking_id)
            return Response({"detail": "Unknown booking."}, status=status.HTTP_404_NOT_FOUND)

        booking = repo.upsert_booking(data, offer_id=existing.get("offer_id"))
        if booking["status"] != existing["status"]:
            repo.record_status_event(
                booking_id=booking["id"],
                status=booking["status"],
                previous_status=existing["status"],
                source="callback",
                payload=data,
            )
        return Response({"status": "ok"})
