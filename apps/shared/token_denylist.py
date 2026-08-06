"""Revocation list for issued JWTs.

``rest_framework_simplejwt``'s own blacklist app needs Django models and a
migration pipeline, neither of which this project has (models are raw-SQL
dataclasses and there is no ``manage.py migrate`` step in the deploy). So
revocation lives in the cache instead: logging out writes the token's ``jti``
under a key that expires exactly when the token itself would have, and every
authentication path checks that key before trusting the token.

The cache is Redis in every environment except local development, so the
denylist survives restarts and is shared across web workers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from django.core.cache import cache
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import Token

logger = logging.getLogger(__name__)

_KEY_PREFIX = "jwt:revoked:"

# Fallback TTL when a token carries no usable `exp`. Longer than the longest
# refresh-token lifetime so a malformed token can never outlive its denylist
# entry.
_MAX_TTL_SECONDS = 60 * 60 * 24 * 31


def _cache_key(jti: str) -> str:
    return f"{_KEY_PREFIX}{jti}"


def _remaining_seconds(payload: dict) -> int:
    """Seconds until the token expires, clamped to a sane range."""
    exp = payload.get("exp")
    if not exp:
        return _MAX_TTL_SECONDS
    try:
        remaining = int(exp) - int(datetime.now(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return _MAX_TTL_SECONDS
    # Already expired: still store briefly to absorb clock skew between hosts.
    return max(60, min(remaining, _MAX_TTL_SECONDS))


def revoke(token: Token) -> bool:
    """Mark ``token`` as unusable for the rest of its lifetime.

    Returns False when the token carries no ``jti`` and therefore cannot be
    tracked — callers should treat that as a failed logout rather than
    silently reporting success.
    """
    jti = token.payload.get("jti")
    if not jti:
        logger.warning("Refusing to revoke a token without a jti claim")
        return False

    cache.set(_cache_key(jti), 1, _remaining_seconds(token.payload))
    return True


def is_revoked(payload: dict) -> bool:
    jti = payload.get("jti")
    if not jti:
        # A token with no jti can never be revoked, so treat it as untrusted
        # rather than letting it bypass the denylist entirely.
        return True
    return cache.get(_cache_key(jti)) is not None


def assert_not_revoked(payload: dict) -> None:
    """Raise ``InvalidToken`` when the token has been revoked."""
    if is_revoked(payload):
        raise InvalidToken(
            {"detail": "Token has been revoked.", "code": "token_revoked"}
        )
