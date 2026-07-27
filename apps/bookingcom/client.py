from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)


class BookingComClientError(Exception):
    pass


class BookingComClient:
    def __init__(
        self,
        *,
        api_url: str,
        property_id: str,
        api_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: int = 20,
        session: requests.Session | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.property_id = property_id
        self.api_token = api_token
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _auth(self) -> tuple[str, str] | None:
        if self.username and self.password:
            return (self.username, self.password)
        return None

    def fetch_reservations(self, *, updated_since: datetime | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"property_id": self.property_id}
        if updated_since is not None:
            params["updated_since"] = updated_since.isoformat()

        response = self.session.get(
            f"{self.api_url}/reservations",
            params=params,
            headers=self._headers(),
            auth=self._auth(),
            timeout=self.timeout,
        )
        if not response.ok:
            raise BookingComClientError(
                f"Booking.com reservation fetch failed with status {response.status_code}"
            )

        body = response.json()
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            if isinstance(body.get("reservations"), list):
                return body["reservations"]
            data = body.get("data")
            if isinstance(data, dict) and isinstance(data.get("reservations"), list):
                return data["reservations"]
        logger.warning("Unexpected Booking.com reservation payload shape: %s", type(body).__name__)
        return []
