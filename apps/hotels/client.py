"""HTTP client for the Hotelios Buyer API (https://docs.hotelios.uz).

Hotelios exposes two families of calls over the same base URL:

  * **Inventory** — `POST /api/v1/hotel` with an `action` naming the reference
    list to fetch (`GetHotelList`, `GetCityList`, …). This is static data that
    changes rarely; it is synced into our own tables.
  * **Booking-Flow** — one endpoint per step of `Search → Quote → Create →
    Confirm`, plus read and cancel. This is live and never cached by us.

Both authenticate the same way: the login, password and access key travel in
every request body. There is no token to mint or refresh.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# The staging host closes idle keep-alive connections without answering, which
# surfaces as RemoteDisconnected partway through a long sync. Retrying the same
# request on a fresh connection is safe for every call here: the inventory
# actions are reads, and the Booking-Flow writes are keyed by `quote_id` and
# `external_id`, so a replayed Create is rejected as a duplicate rather than
# producing a second booking.
TRANSPORT_RETRIES = 2
TRANSPORT_RETRY_BACKOFF_SECONDS = 1.0

# Codes worth naming, from the ErrorCode table in the OpenAPI description.
ERROR_INVALID_CREDENTIALS = 1003
ERROR_NOT_FOUND = 1016
ERROR_ALREADY_PROCESSED = 1015
ERROR_TOO_MANY_REQUESTS = 2007
ERROR_BOOKING_FAILED = 4300
ERROR_PRICE_CHANGED = 4301
ERROR_NO_ROOMS = 4302
ERROR_INSUFFICIENT_BALANCE = 4303

RETRYABLE_ERROR_CODES = frozenset({
    2000,  # server error
    2006,  # connection timeout
    ERROR_TOO_MANY_REQUESTS,
})


class HoteliosError(Exception):
    """A Hotelios call that answered `success: false`, or failed in transport."""

    def __init__(
        self,
        message: str,
        *,
        error_code: int | None = None,
        status_code: int | None = None,
        action: str | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.action = action
        self.payload = payload

    @property
    def is_not_found(self) -> bool:
        return self.error_code == ERROR_NOT_FOUND

    @property
    def is_price_changed(self) -> bool:
        return self.error_code == ERROR_PRICE_CHANGED

    @property
    def is_sold_out(self) -> bool:
        return self.error_code == ERROR_NO_ROOMS

    @property
    def is_retryable(self) -> bool:
        return self.error_code in RETRYABLE_ERROR_CODES

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        parts = [self.message]
        if self.error_code is not None:
            parts.append(f"code={self.error_code}")
        if self.action:
            parts.append(f"action={self.action}")
        return " | ".join(parts)


class HoteliosClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        login: str | None = None,
        password: str | None = None,
        access_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.HOTELIOS_BASE_URL or "").rstrip("/") + "/"
        self.login = login or settings.HOTELIOS_LOGIN
        self.password = password or settings.HOTELIOS_PASSWORD
        self.access_key = access_key or settings.HOTELIOS_ACCESS_KEY
        self.timeout = timeout or settings.HOTELIOS_TIMEOUT_SECONDS
        self._session = requests.Session()

    # -- transport --------------------------------------------------------

    def _credentials(self) -> dict[str, str]:
        if not (self.login and self.password and self.access_key):
            raise HoteliosError(
                "Hotelios credentials are not configured. Set HOTELIOS_LOGIN, "
                "HOTELIOS_PASSWORD and HOTELIOS_ACCESS_KEY."
            )
        return {
            "login": self.login,
            "password": self.password,
            "access_key": self.access_key,
        }

    def _post(self, path: str, body: dict[str, Any], *, action: str | None = None) -> dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        payload_body = {**self._credentials(), **body}
        headers = {
            # Hotelios validates the charset on the content type and answers
            # 2002 when it is missing.
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }

        response = None
        for attempt in range(TRANSPORT_RETRIES + 1):
            try:
                response = self._session.post(
                    url, json=payload_body, headers=headers, timeout=self.timeout
                )
                break
            except requests.RequestException as exc:
                if attempt >= TRANSPORT_RETRIES:
                    raise HoteliosError(
                        f"Hotelios is unreachable: {exc}", action=action
                    ) from exc
                logger.info(
                    "hotels: %s failed in transport (%s); retrying on a new connection.",
                    action or path,
                    exc,
                )
                # A dropped keep-alive connection stays poisoned in the pool.
                self._session.close()
                self._session = requests.Session()
                time.sleep(TRANSPORT_RETRY_BACKOFF_SECONDS * (attempt + 1))

        try:
            payload = response.json()
        except ValueError:
            raise HoteliosError(
                f"Hotelios returned a non-JSON response (HTTP {response.status_code}).",
                status_code=response.status_code,
                action=action,
            ) from None

        if not isinstance(payload, dict):
            raise HoteliosError(
                "Hotelios returned an unexpected response shape.",
                status_code=response.status_code,
                action=action,
                payload=payload,
            )

        if not payload.get("success"):
            # The two families spell the error code differently: inventory
            # replies carry `code`, Booking-Flow replies `errorCode`.
            error_code = payload.get("errorCode")
            if error_code is None:
                error_code = payload.get("code")
            raise HoteliosError(
                payload.get("description") or f"Hotelios refused the {action or path} call.",
                error_code=error_code,
                status_code=response.status_code,
                action=action,
                payload=payload,
            )

        return payload.get("data") or {}

    # -- inventory --------------------------------------------------------

    def inventory(
        self,
        action: str,
        data: dict[str, Any] | None = None,
        *,
        version: int = 1,
    ) -> dict[str, Any]:
        """One `POST /api/v1/hotel` reference call."""
        body: dict[str, Any] = {"action": action, "version": version}
        if data:
            body["data"] = data
        return self._post("api/v1/hotel", body, action=action)

    def get_country_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetCountryList").get("countries") or []

    def get_region_list(self, *, country_id: int | None = None) -> list[dict[str, Any]]:
        data = {"country_id": country_id} if country_id is not None else None
        return self.inventory("GetRegionList", data).get("regions") or []

    def get_city_list(self, *, region_id: int | None = None) -> list[dict[str, Any]]:
        data = {"region_id": region_id} if region_id is not None else None
        return self.inventory("GetCityList", data).get("cities") or []

    def get_hotel_type_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetHotelTypeList").get("hotel_types") or []

    def get_facility_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetFacilityList").get("facilities") or []

    def get_equipment_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetEquipmentList").get("equipment_list") or []

    def get_nearby_place_type_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetNearbyPlacesTypeList").get("nearby_place_types") or []

    def get_services_in_room_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetServicesInRoomList").get("services_in_room") or []

    def get_bed_type_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetBedTypeList").get("bed_types") or []

    def get_star_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetStarList").get("stars") or []

    def get_currency_list(self) -> list[dict[str, Any]]:
        return self.inventory("GetCurrencyList").get("currency_list") or []

    def get_hotel_page(
        self,
        *,
        page: int = 1,
        hotel_ids: list[int] | None = None,
        hotel_type_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """One page of `GetHotelList`, with its pagination block.

        Since the November 2024 revision this response carries the hotel's
        photos, facilities and nearby places inline, so importing a hotel takes
        exactly one call rather than four.
        """
        data: dict[str, Any] = {"page": page}
        if hotel_ids:
            data["hotel_ids"] = hotel_ids
        if hotel_type_id is not None:
            data["hotel_type_id"] = hotel_type_id
        result = self.inventory("GetHotelList", data)
        return result.get("hotels") or [], result.get("pagination") or {}

    def get_room_type_page(
        self,
        *,
        page: int = 1,
        hotel_ids: list[int] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """One page of `GetHotelRoomTypeList`, photos and equipment included."""
        data: dict[str, Any] = {"page": page}
        if hotel_ids:
            data["hotel_ids"] = hotel_ids
        result = self.inventory("GetHotelRoomTypeList", data)
        return result.get("hotel_room_types") or [], result.get("pagination") or {}

    def get_hotel_services_in_room(self, *, hotel_id: int) -> list[dict[str, Any]]:
        return self.inventory(
            "GetHotelServicesInRoomList", {"hotel_id": hotel_id}
        ).get("hotel_services_in_room") or []

    # -- booking flow -----------------------------------------------------

    def search(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """POST /api/v1/booking-flow/search. May be answered from their cache."""
        result = self._post("api/v1/booking-flow/search", {"data": data}, action="search")
        return result.get("hotels") or []

    def quote(self, option_ref_ids: list[str]) -> dict[str, Any]:
        """POST /api/v1/booking-flow/quote — mandatory, and always live.

        Returns a `quote_id` with a lifetime Hotelios only guarantees for an
        hour, so this belongs immediately before the booking attempt.
        """
        return self._post(
            "api/v1/booking-flow/quote",
            {"data": {"options": [{"option_ref_id": ref} for ref in option_ref_ids]}},
            action="quote",
        )

    def create_booking(
        self,
        *,
        quote_id: str,
        external_id: str,
        booking_rooms: list[dict[str, Any]],
        comment: str | None = None,
        delta_price: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/booking-flow/booking/create — holds, does not confirm."""
        data: dict[str, Any] = {
            "quote_id": quote_id,
            "external_id": external_id,
            "booking_rooms": booking_rooms,
        }
        if comment:
            data["comment"] = comment
        if delta_price:
            data["deltaPrice"] = delta_price
        return self._post(
            "api/v1/booking-flow/booking/create", {"data": data}, action="booking/create"
        )

    def confirm_booking(self, booking_id: str) -> dict[str, Any]:
        """POST /api/v1/booking-flow/booking/confirm — sends it to the hotel.

        Confirming twice is an error, not a no-op.
        """
        return self._post(
            "api/v1/booking-flow/booking/confirm",
            {"data": {"booking_id": booking_id}},
            action="booking/confirm",
        )

    def cancel_booking(self, booking_id: str) -> dict[str, Any]:
        """POST /api/v1/booking-flow/booking/cancel."""
        return self._post(
            "api/v1/booking-flow/booking/cancel",
            {"data": {"booking_id": booking_id}},
            action="booking/cancel",
        )

    def read_booking(
        self,
        *,
        booking_id: str | None = None,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/booking-flow/booking/read, by either identifier."""
        if not (booking_id or external_id):
            raise HoteliosError("read_booking needs a booking_id or an external_id.")
        data = {"booking_id": booking_id} if booking_id else {"external_id": external_id}
        return self._post(
            "api/v1/booking-flow/booking/read", {"data": data}, action="booking/read"
        )

    def get_balance(self) -> dict[str, Any]:
        """POST /api/v1/accounting/balance — the credit bookings are drawn on."""
        return self._post("api/v1/accounting/balance", {}, action="accounting/balance")


def get_client() -> HoteliosClient:
    return HoteliosClient()
