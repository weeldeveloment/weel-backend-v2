"""Encryption for the SMTP passwords we hold on employees' behalf.

Sending mail as ``aziz@kompaniya.com`` means authenticating to Mailcow as that
mailbox, so the password has to be recoverable — it cannot be hashed. Fernet
(AES-128-CBC + HMAC) keeps it unreadable in a database dump while still being
decryptable by a process holding ``B2B_MAIL_SECRET_KEY``.

The password is generated here and never leaves the server: no API response
returns it, and an employee who wants to use Thunderbird gets a fresh one
issued rather than the stored one revealed.
"""
from __future__ import annotations

import secrets
import string

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# Ambiguous characters are left out. These passwords get retyped by hand into
# phone mail clients often enough that l/I/1 and O/0 confusion is a real
# support cost, and the length more than covers the lost entropy.
_ALPHABET = (
    string.ascii_lowercase.replace("l", "").replace("o", "")
    + string.ascii_uppercase.replace("I", "").replace("O", "")
    + "23456789"
    + "!@#$%^*-_=+"
)


def _fernet():
    from cryptography.fernet import Fernet

    key = getattr(settings, "B2B_MAIL_SECRET_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "B2B_MAIL_SECRET_KEY is not set — corporate mail cannot store "
            "mailbox passwords without it. Generate one with "
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


def generate_password(length: int = 24) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Recover a stored mailbox password.

    Raises ``ValueError`` rather than the library's ``InvalidToken`` so callers
    — the send task, mostly — can tell "this row predates a key rotation" apart
    from a transport failure and stop retrying it.
    """
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Stored mailbox password could not be decrypted. It was most "
            "likely encrypted under a previous B2B_MAIL_SECRET_KEY; the "
            "mailbox needs its password reset."
        ) from exc
