"""Encryption for the access tokens we hold on a workspace's behalf.

Same reasoning as `apps/b2b/mail/crypto.py`: to read a company's lead forms we
have to call Meta as them, so the token has to be recoverable and cannot be
hashed. Fernet keeps it unreadable in a database dump.

The key falls back to `B2B_MAIL_SECRET_KEY` when no integrations key is set.
A deployment that already runs mail has a Fernet key configured and should not
have to invent a second one before it can connect a Facebook page; a
deployment that wants them separated sets `B2B_INTEGRATIONS_SECRET_KEY` and
gets its own.

Nothing ever returns one of these: no serializer includes a token, and there
is no endpoint that reads one back out.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _key() -> str:
    key = (getattr(settings, "B2B_INTEGRATIONS_SECRET_KEY", "") or "").strip()
    return key or (getattr(settings, "B2B_MAIL_SECRET_KEY", "") or "").strip()


def is_configured() -> bool:
    return bool(_key())


def _fernet():
    from cryptography.fernet import Fernet

    key = _key()
    if not key:
        raise ImproperlyConfigured(
            "B2B_INTEGRATIONS_SECRET_KEY (or B2B_MAIL_SECRET_KEY) is not set — "
            "an integration cannot store an access token without it. Generate "
            "one with `python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'`."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "The integrations secret key is not a valid Fernet key (32 "
            "url-safe base64-encoded bytes)."
        ) from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str:
    """Recover a stored token.

    Raises ``ValueError`` rather than the library's ``InvalidToken`` so the
    ingest path can tell "this row predates a key rotation, stop retrying and
    ask them to reconnect" apart from a transport failure.
    """
    from cryptography.fernet import InvalidToken

    if not ciphertext:
        raise ValueError("No token stored — the integration needs reconnecting.")
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "The stored access token could not be decrypted. It was most "
            "likely encrypted under a previous key; reconnect the integration."
        ) from exc
