"""Encryption for the credentials we hold on employees' behalf.

To read and send as somebody's inbox we have to authenticate to their provider
as them, so the app password or Google refresh token has to be recoverable — it
cannot be hashed. Fernet (AES-128-CBC + HMAC) keeps it unreadable in a database
dump while still being decryptable by a process holding
``B2B_MAIL_SECRET_KEY``.

These are somebody's *personal* credentials to a service we do not run, which
is why nothing ever returns one: no API response includes it, and there is no
endpoint that can read one back out.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

def _fernet():
    from cryptography.fernet import Fernet

    key = getattr(settings, "B2B_MAIL_SECRET_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "B2B_MAIL_SECRET_KEY is not set — mail cannot store account "
            "credentials without it. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'`."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "B2B_MAIL_SECRET_KEY is not a valid Fernet key (32 url-safe "
            "base64-encoded bytes)."
        ) from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Recover a stored credential.

    Raises ``ValueError`` rather than the library's ``InvalidToken`` so callers
    — the send task, mostly — can tell "this row predates a key rotation" apart
    from a transport failure and stop retrying it.
    """
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Stored credential could not be decrypted. It was most likely "
            "encrypted under a previous B2B_MAIL_SECRET_KEY; the account "
            "needs to be reconnected."
        ) from exc
