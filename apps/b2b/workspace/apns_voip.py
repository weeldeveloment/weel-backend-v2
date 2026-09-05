"""Apple's VoIP push — the only thing that rings a closed iPhone.

An ordinary alert push sits in the tray until somebody taps it, and by then
a ring is usually over. A PushKit push wakes the app straight away and the
app hands the call to CallKit, which draws the phone's own incoming-call
screen — ringtone, lock screen and all — exactly as a phone call would.

Sent to APNs directly over HTTP/2 rather than through FCM, which cannot send
the ``voip`` push type. Authenticated with a provider token: a JWT signed
with the .p8 key from the Apple developer account, which APNs accepts for
up to an hour — it is cached for fifty minutes here.

Apple's rules for this push, and why the code looks the way it does:

* every VoIP push *must* end in a call reported to CallKit, or iOS stops
  delivering them and kills the app — so this is sent for a ring and for
  nothing else. A ring that is cancelled is not "un-pushed"; the CallKit
  screen times out on its own after the ring window;
* ``apns-expiration`` is the ring window: a phone that was out of reach
  must not be rung for a call that is already written down as missed;
* a 400/410 naming the token as dead is final, and the token is dropped so
  the next ring goes through FCM instead of failing again.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import httpx
import jwt
from django.conf import settings

logger = logging.getLogger(__name__)

#: How long a provider token is reused. APNs allows an hour.
PROVIDER_TOKEN_TTL_SECONDS = 50 * 60

#: Reasons APNs gives for a token that will never work again.
DEAD_TOKEN_REASONS = frozenset(
    {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic", "ExpiredToken"}
)

_lock = threading.Lock()
_provider_token: tuple[str, float] | None = None
_client: httpx.Client | None = None


def is_configured() -> bool:
    """Whether the four settings a VoIP push needs are all present."""
    return bool(
        settings.APNS_TEAM_ID
        and settings.APNS_KEY_ID
        and (settings.APNS_AUTH_KEY or settings.APNS_AUTH_KEY_FILE)
        and settings.APNS_VOIP_TOPIC
    )


def gateway() -> str:
    return (
        "https://api.sandbox.push.apple.com"
        if settings.APNS_USE_SANDBOX
        else "https://api.push.apple.com"
    )


def _private_key() -> str:
    if settings.APNS_AUTH_KEY_FILE:
        with open(settings.APNS_AUTH_KEY_FILE, encoding="utf-8") as handle:
            return handle.read()
    return settings.APNS_AUTH_KEY.replace("\\n", "\n").strip()


def provider_token(now: float | None = None) -> str:
    """The bearer token for APNs, minted or reused."""
    global _provider_token
    now = time.time() if now is None else now
    with _lock:
        if _provider_token and now - _provider_token[1] < PROVIDER_TOKEN_TTL_SECONDS:
            return _provider_token[0]
        token = jwt.encode(
            {"iss": settings.APNS_TEAM_ID, "iat": int(now)},
            _private_key(),
            algorithm="ES256",
            headers={"kid": settings.APNS_KEY_ID},
        )
        _provider_token = (token, now)
        return token


def _forget_provider_token() -> None:
    global _provider_token
    with _lock:
        _provider_token = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(http2=True, timeout=10.0)
    return _client


def use_client_for_tests(client: httpx.Client | None) -> None:
    """Swaps the HTTP client — a test hands in one with a mock transport."""
    global _client
    _client = client
    _forget_provider_token()


def send(
    device_token: str,
    payload: dict[str, Any],
    *,
    ttl_seconds: int,
    on_dead_token: Callable[[str], None] | None = None,
) -> bool:
    """Delivers one VoIP push. True when APNs accepted it."""
    if not is_configured() or not device_token:
        return False
    headers = {
        "authorization": f"bearer {provider_token()}",
        "apns-topic": settings.APNS_VOIP_TOPIC,
        "apns-push-type": "voip",
        "apns-priority": "10",
        "apns-expiration": str(int(time.time()) + max(1, int(ttl_seconds))),
    }
    try:
        response = _http().post(
            f"{gateway()}/3/device/{device_token}", json=payload, headers=headers
        )
    except httpx.HTTPError as error:
        logger.warning("APNs VoIP push did not go out: %s", error)
        return False
    if response.status_code == 200:
        return True

    reason = None
    try:
        reason = response.json().get("reason")
    except ValueError:
        pass
    if response.status_code in (400, 410) and reason in DEAD_TOKEN_REASONS:
        if on_dead_token:
            on_dead_token(device_token)
    elif response.status_code == 403:
        # A provider token APNs no longer likes: minted afresh next time.
        _forget_provider_token()
    logger.warning("APNs VoIP push refused: %s %s", response.status_code, reason)
    return False
