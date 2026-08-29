"""HTTP client for the Bookhara Avia API (https://docs.bookhara.uz).

Every response Bookhara sends is the same envelope — `request_id`, `created_at`,
`message` and either `data` or an `error_code`/`errors` pair — so the transport
here unwraps `data` for callers and turns everything else into `BookharaError`.

Authorization is a Bearer token minted from the account's email/password. The
token is long-lived, so it lives in the shared cache rather than being minted
per request; a 401 drops the cached copy and retries once, which is what makes
a token rotated on Bookhara's side self-heal instead of hard-failing a search.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = "bookhara:avia:access_token"

# The issued token carries a ~30-day expiry. Caching for a day keeps the mint
# call rare while staying far enough from the boundary that a cached token is
# never handed out moments before it dies mid-flight.
TOKEN_CACHE_TTL_SECONDS = 60 * 60 * 24

# Offer prices moved under us. The docs call these out as "re-read the offer
# and try again" rather than as a failed booking.
PRICE_CHANGED_ERROR_CODES = {1030, 1031, 100500}

# Duplicate-booking guards. Bookhara answers with the id of the booking the
# request duplicates, which is the only useful thing to show the caller.
DUPLICATE_UNPAID_ERROR_CODE = 5231
DUPLICATE_PAID_ERROR_CODE = 5232
DUPLICATE_ERROR_CODES = {DUPLICATE_UNPAID_ERROR_CODE, DUPLICATE_PAID_ERROR_CODE}

# Refund was allowed by the fare rules but is not actually available right now.
# 5233 covers the penalty refund, 5234 the penalty-free VOID; in both cases the
# remaining option is the call-centre request, not a retry.
REFUND_UNAVAILABLE_ERROR_CODE = 5233
VOID_UNAVAILABLE_ERROR_CODE = 5234
REFUND_UNAVAILABLE_ERROR_CODES = {
    REFUND_UNAVAILABLE_ERROR_CODE,
    VOID_UNAVAILABLE_ERROR_CODE,
}

# Bookhara answers HTTP 410 for two very different things. Most of the time it
# means "the carrier has not confirmed yet, repeat the call" — but the booking
# endpoint also returns 410 for passenger data it will never accept, and
# retrying those forever is the wrong answer. Verified against the dev API:
# a surname of 15 characters comes back as 410 with error_code 1154, and no
# number of retries changes that.
#
# Everything here is a permanent rejection from the "Ошибки поиска и
# бронирования авиабилетов" table in docs.bookhara.uz/errors.
PERMANENT_410_ERROR_CODES = {
    1011,  # infant older than two
    1018, 1019, 1020, 1021, 1022,  # a required passenger field is missing
    1023, 1024,  # payer email / phone missing
    1025,  # invalid data format
    1124,  # first/last name must be 1-25 characters
    1125,  # name + surname + birthdate together are too long
    1126,  # passenger data incomplete for the requested passenger counts
    1127,  # passenger is on the airline blacklist
    1129, 1130, 1131, 1132, 1133, 1134, 1135, 1136, 1137, 1138,
    1145, 1146, 1147,
    1148, 1149, 1150, 1151, 1152,
    1153,  # document issuing country wrong for this document type
    1154,  # passenger data is invalid
    1155,  # Cyrillic is not allowed for this document type
    1156,  # check names and document numbers
    1157, 1158, 1159, 1160, 1161, 1162, 1163,  # citizenship not allowed
    1168,  # wrong passenger types
    1169,  # invalid document expiry
    1183,  # the adult accompanying an infant must be 18+
    5237,  # a third order for these passengers is refused for 24 hours
}


class BookharaError(Exception):
    """A Bookhara call that did not come back with a usable `data` block."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        request_id: str | None = None,
        errors: Any = None,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.request_id = request_id
        self.errors = errors
        self.data = data

    @property
    def is_price_changed(self) -> bool:
        return self.error_code in PRICE_CHANGED_ERROR_CODES

    @property
    def is_duplicate_booking(self) -> bool:
        return self.error_code in DUPLICATE_ERROR_CODES

    @property
    def is_refund_unavailable(self) -> bool:
        """The fare allows a refund on paper, but not one we can take now."""
        return self.error_code in REFUND_UNAVAILABLE_ERROR_CODES

    @property
    def existing_booking_id(self) -> str | None:
        if isinstance(self.data, dict):
            return self.data.get("existing_booking_id")
        return None

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        parts = [self.message]
        if self.error_code is not None:
            parts.append(f"error_code={self.error_code}")
        if self.status_code is not None:
            parts.append(f"http={self.status_code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " | ".join(parts)


class BookharaExpiredError(BookharaError):
    """The offer or booking no longer exists — Bookhara answers 404.

    Offers live from a few hours to a day; a booking disappears once its
    `expire` auto-cancellation time passes. Either way the caller has to search
    again rather than retry the same id.
    """


class BookharaUnconfirmedError(BookharaError):
    """The carrier did not confirm the operation — Bookhara answers 410.

    The docs say to retry these: seats, price and cancellation confirmations
    all surface this way when the upstream GDS is slow to answer.

    Not every 410 is one of these — see `PERMANENT_410_ERROR_CODES` and
    `BookharaRejectedError`, which is what a 410 carrying a passenger-data
    error code becomes instead.
    """


class BookharaRejectedError(BookharaError):
    """Bookhara will not accept this request, however many times we send it.

    The booking endpoint answers HTTP 410 for bad passenger data as well as
    for an unconfirmed carrier, and the two need opposite handling: this one
    has to reach the person filling in the form, because only they can fix it.
    """


def _flatten_params(params: Any, prefix: str = "") -> dict[str, Any]:
    """Encode nested lists/dicts the way Bookhara's query strings expect.

    `directions=[{"departure_airport": "TAS"}]` becomes
    `directions[0][departure_airport]=TAS`. `requests` will not do this on its
    own, and the search endpoint takes its whole route this way.
    """
    flat: dict[str, Any] = {}
    if isinstance(params, dict):
        for key, value in params.items():
            if value is None:
                continue
            child = f"{prefix}[{key}]" if prefix else str(key)
            flat.update(_flatten_params(value, child))
    elif isinstance(params, (list, tuple)):
        for index, value in enumerate(params):
            if value is None:
                continue
            flat.update(_flatten_params(value, f"{prefix}[{index}]"))
    else:
        value = params
        if isinstance(value, bool):
            value = 1 if value else 0
        flat[prefix] = value
    return flat


class BookharaClient:
    """Thin transport over the Bookhara Avia REST API.

    One instance is cheap — it holds a `requests.Session` for connection reuse
    and reads the token from the shared cache, so instances do not need to be
    long-lived or shared between threads.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        email: str | None = None,
        password: str | None = None,
        access_type: str | None = None,
        timeout: float | None = None,
        language: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.BOOKHARA_BASE_URL or "").rstrip("/") + "/"
        self.email = email or settings.BOOKHARA_EMAIL
        self.password = password or settings.BOOKHARA_PASSWORD
        self.access_type = access_type or settings.BOOKHARA_ACCESS_TYPE
        self.timeout = timeout or settings.BOOKHARA_TIMEOUT_SECONDS
        self.language = language
        self._session = requests.Session()

    # -- authorization ----------------------------------------------------

    def _mint_token(self) -> str:
        if not (self.email and self.password):
            raise BookharaError(
                "Bookhara credentials are not configured. "
                "Set BOOKHARA_EMAIL and BOOKHARA_PASSWORD."
            )
        url = urljoin(self.base_url, "api/v1/accounts/tokens")
        try:
            response = self._session.post(
                url,
                json={
                    "email": self.email,
                    "password": self.password,
                    "access_type": self.access_type,
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BookharaError(f"Bookhara is unreachable: {exc}") from exc

        payload = self._decode(response)
        if response.status_code != 200:
            raise self._error_from(response, payload)

        token = (payload.get("data") or {}).get("token")
        if not token:
            raise BookharaError(
                "Bookhara did not return an access token.",
                status_code=response.status_code,
                request_id=payload.get("request_id"),
            )
        cache.set(TOKEN_CACHE_KEY, token, TOKEN_CACHE_TTL_SECONDS)
        return token

    def _token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = cache.get(TOKEN_CACHE_KEY)
            if cached:
                return cached
        return self._mint_token()

    # -- transport --------------------------------------------------------

    @staticmethod
    def _decode(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {"data": payload}

    @staticmethod
    def _error_from(response: requests.Response, payload: dict[str, Any]) -> BookharaError:
        message = payload.get("message") or (
            f"Bookhara returned HTTP {response.status_code}."
        )
        kwargs = {
            "status_code": response.status_code,
            "error_code": payload.get("error_code"),
            "request_id": payload.get("request_id"),
            "errors": payload.get("errors"),
            "data": payload.get("data"),
        }
        if response.status_code == 404:
            return BookharaExpiredError(message, **kwargs)
        if response.status_code == 410:
            if payload.get("error_code") in PERMANENT_410_ERROR_CODES:
                return BookharaRejectedError(message, **kwargs)
            return BookharaUnconfirmedError(message, **kwargs)
        return BookharaError(message, **kwargs)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        language: str | None = None,
        _retry_auth: bool = True,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token()}",
        }
        accept_language = language or self.language
        if accept_language:
            headers["Accept-Language"] = accept_language

        try:
            response = self._session.request(
                method,
                url,
                params=_flatten_params(params) if params else None,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BookharaError(f"Bookhara is unreachable: {exc}") from exc

        # A rotated or revoked token is indistinguishable from a stale cache
        # entry here, so mint a fresh one once and replay the call.
        if response.status_code in (401, 403) and _retry_auth:
            logger.info("Bookhara rejected the cached token; re-minting once.")
            cache.delete(TOKEN_CACHE_KEY)
            self._token(force_refresh=True)
            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                language=language,
                _retry_auth=False,
            )

        payload = self._decode(response)
        if response.status_code != 200 or payload.get("error_code") is not None:
            raise self._error_from(response, payload)
        return payload.get("data")

    # -- offers -----------------------------------------------------------

    def search_offers(
        self,
        *,
        directions: list[dict[str, str]],
        service_class: str,
        adults: int,
        children: int = 0,
        infants: int = 0,
        infants_with_seat: int = 0,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /api/v1/offers — one entry per bookable itinerary."""
        return self._request(
            "GET",
            "api/v1/offers",
            params={
                "directions": directions,
                "service_class": service_class,
                "adults": adults,
                "children": children,
                "infants": infants,
                "infants_with_seat": infants_with_seat,
            },
            language=language,
        ) or []

    def get_offer(
        self,
        offer_id: str,
        *,
        with_additional_services: bool = False,
        language: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/offers/{id} — re-price and re-check seats before booking."""
        params = {"with-additional-services": 1} if with_additional_services else None
        return self._request(
            "GET", f"api/v1/offers/{offer_id}", params=params, language=language
        )

    def get_fare_family(self, offer_id: str, *, language: str | None = None) -> list[dict[str, Any]]:
        """GET /api/v1/offers/{id}/fare-family — only for offers with is_fare_family."""
        return self._request(
            "GET", f"api/v1/offers/{offer_id}/fare-family", language=language
        ) or []

    def get_offer_rules(self, offer_id: str, *, language: str | None = None) -> list[dict[str, Any]]:
        """GET /api/v1/offers/{id}/rules — fare conditions, per direction."""
        return self._request("GET", f"api/v1/offers/{offer_id}/rules", language=language) or []

    # -- booking ----------------------------------------------------------

    def create_booking(
        self,
        offer_id: str,
        *,
        payer_name: str,
        payer_email: str,
        payer_tel: str,
        passengers: list[dict[str, Any]],
        order_note: str | None = None,
        additional_services: list[str] | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/offers/{id}/booking."""
        body: dict[str, Any] = {
            "payer_name": payer_name,
            "payer_email": payer_email,
            "payer_tel": payer_tel,
            "passengers": passengers,
        }
        if order_note:
            body["order_note"] = order_note
        if additional_services:
            body["additional_services"] = additional_services
        return self._request(
            "POST", f"api/v1/offers/{offer_id}/booking", json_body=body, language=language
        )

    def get_booking(self, booking_id: str, *, language: str | None = None) -> dict[str, Any]:
        """GET /api/v1/booking/{id} — the authoritative state of an order."""
        return self._request("GET", f"api/v1/booking/{booking_id}", language=language)

    def get_booking_rules(self, booking_id: str, *, language: str | None = None) -> list[dict[str, Any]]:
        """GET /api/v1/booking/{id}/rules."""
        return self._request("GET", f"api/v1/booking/{booking_id}/rules", language=language) or []

    def check_booking_price(self, booking_id: str) -> dict[str, Any]:
        """GET /api/v1/booking/{id}/check-price."""
        return self._request("GET", f"api/v1/booking/{booking_id}/check-price")

    def payment_permission(self, booking_id: str) -> dict[str, Any]:
        """GET /api/v1/booking/{id}/payment-permission."""
        return self._request("GET", f"api/v1/booking/{booking_id}/payment-permission")

    def pay_booking(self, booking_id: str) -> dict[str, Any]:
        """POST /api/v1/booking/{id}/payment — draws on the Bookhara deposit."""
        return self._request("POST", f"api/v1/booking/{booking_id}/payment")

    def get_fiscalization(self, booking_id: str) -> dict[str, Any]:
        """GET /api/v1/booking/{id}/fiscalization — paid/ticketed orders only."""
        return self._request("GET", f"api/v1/booking/{booking_id}/fiscalization")

    def get_pdf_receipt(self, booking_id: str) -> list[dict[str, Any]]:
        """GET /api/v1/booking/{id}/pdf-receipt — one receipt per passenger."""
        return self._request("GET", f"api/v1/booking/{booking_id}/pdf-receipt") or []

    # -- cancellation and refunds -----------------------------------------

    def cancel_unpaid(self, booking_id: str) -> dict[str, Any]:
        """DELETE /api/v1/booking/{id}/cancel-unpaid — for status `booked`."""
        return self._request("DELETE", f"api/v1/booking/{booking_id}/cancel-unpaid")

    def void(self, booking_id: str) -> dict[str, Any]:
        """DELETE /api/v1/booking/{id}/void — full refund, no penalty."""
        return self._request("DELETE", f"api/v1/booking/{booking_id}/void")

    def get_refund_amounts(self, booking_id: str) -> dict[str, Any]:
        """GET /api/v1/booking/{id}/get-refund-amounts — refund minus penalty."""
        return self._request("GET", f"api/v1/booking/{booking_id}/get-refund-amounts")

    def auto_cancel(self, booking_id: str) -> dict[str, Any]:
        """DELETE /api/v1/booking/{id}/auto-cancel — refund with penalty."""
        return self._request("DELETE", f"api/v1/booking/{booking_id}/auto-cancel")

    def manual_refund(self, booking_id: str) -> dict[str, Any]:
        """DELETE /api/v1/booking/{id}/manual-refund — hand off to the call centre."""
        return self._request("DELETE", f"api/v1/booking/{booking_id}/manual-refund")

    # -- account and reference --------------------------------------------

    def check_balance(self) -> dict[str, Any]:
        """GET /api/v1/accounts/check-balance — deposit available for payments."""
        return self._request("GET", "api/v1/accounts/check-balance")

    def get_schedule(
        self,
        *,
        departure_from: str,
        departure_to: str,
        airport_from: str | None = None,
        airport_to: str | None = None,
        airlines: list[str] | None = None,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /api/v1/services/schedule — published flights, not priced offers."""
        return self._request(
            "GET",
            "api/v1/services/schedule",
            params={
                "departure_from": departure_from,
                "departure_to": departure_to,
                "airport_from": airport_from,
                "airport_to": airport_to,
                "airlines": airlines,
            },
            language=language,
        ) or []


def get_client(*, language: str | None = None) -> BookharaClient:
    return BookharaClient(language=language)
