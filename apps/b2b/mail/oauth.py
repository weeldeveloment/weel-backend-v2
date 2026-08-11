"""Google sign-in for connecting a Gmail inbox.

The nicer of the two ways to connect an account: one button, no password
stored, and the user can revoke us from their Google account page.

**This path stays behind ``B2B_MAIL_GOOGLE_OAUTH_ENABLED`` until Google has
verified the app.** Reading someone's mail is a *restricted* scope: until
verification (which includes a CASA security assessment) is granted, Google
caps the app at 100 test users and shows everyone else a blocked-app screen.
The app-password path in ``providers.py`` is what works in the meantime, and
it stays afterwards for every provider that is not Gmail.

What we ask for and why:

* ``mail.google.com`` — full IMAP/SMTP access, which is what
  ``imap_sync``/``smtp_send`` need. Narrower scopes exist, but they only work
  through the Gmail REST API, not IMAP, and would mean a second sync
  implementation for one provider.
* ``userinfo.email`` — to learn which address was actually granted, rather
  than trusting what the user typed.

The refresh token is what gets stored (encrypted); access tokens last an hour
and are fetched as needed.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/userinfo.email",
]


class OAuthError(RuntimeError):
    pass


def is_enabled() -> bool:
    return bool(
        getattr(settings, "B2B_MAIL_GOOGLE_OAUTH_ENABLED", False)
        and getattr(settings, "B2B_MAIL_GOOGLE_CLIENT_ID", "")
        and getattr(settings, "B2B_MAIL_GOOGLE_CLIENT_SECRET", "")
    )


def _config() -> tuple[str, str, str]:
    client_id = getattr(settings, "B2B_MAIL_GOOGLE_CLIENT_ID", "")
    client_secret = getattr(settings, "B2B_MAIL_GOOGLE_CLIENT_SECRET", "")
    redirect_uri = getattr(settings, "B2B_MAIL_GOOGLE_REDIRECT_URI", "")
    if not (client_id and client_secret and redirect_uri):
        raise ImproperlyConfigured(
            "B2B_MAIL_GOOGLE_CLIENT_ID, _CLIENT_SECRET and _REDIRECT_URI must "
            "all be set before Google sign-in can be offered."
        )
    return client_id, client_secret, redirect_uri


def authorize_url(state: str) -> str:
    """Where to send the browser. ``state`` ties the callback back to a session."""
    client_id, _, redirect_uri = _config()
    return f"{AUTH_URL}?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # Without `offline` there is no refresh token, and the connection would
        # silently stop working an hour later.
        "access_type": "offline",
        # Google only issues a refresh token the first time a user consents.
        # Someone reconnecting after removing the account would otherwise get
        # an access token with no way to renew it.
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    })


def exchange_code(code: str) -> dict:
    """Trade the callback's code for tokens plus the address that was granted."""
    client_id, client_secret, redirect_uri = _config()
    try:
        response = requests.post(TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=20)
    except requests.RequestException as exc:
        raise OAuthError(f"Could not reach Google: {exc}") from exc

    if response.status_code >= 400:
        raise OAuthError(f"Google rejected the sign-in: {response.text[:300]}")

    payload = response.json()
    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token")
    if not refresh_token or not access_token:
        raise OAuthError(
            "Google did not return a refresh token. The account may already be "
            "connected — remove it from the Google account page and try again."
        )

    return {
        "refresh_token": refresh_token,
        "access_token": access_token,
        "expires_at": timezone.now() + timezone.timedelta(
            seconds=int(payload.get("expires_in", 3600))
        ),
        "address": _email_for(access_token),
    }


def refresh_access_token(refresh_token: str) -> dict:
    client_id, client_secret, _ = _config()
    try:
        response = requests.post(TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        }, timeout=20)
    except requests.RequestException as exc:
        raise OAuthError(f"Could not reach Google: {exc}") from exc

    if response.status_code >= 400:
        # A revoked grant is permanent — the user removed us from their Google
        # account, and the only fix is reconnecting.
        raise OAuthError(f"Google refused to refresh the token: {response.text[:300]}")

    payload = response.json()
    return {
        "access_token": payload["access_token"],
        "expires_at": timezone.now() + timezone.timedelta(
            seconds=int(payload.get("expires_in", 3600))
        ),
    }


def _email_for(access_token: str) -> str:
    try:
        response = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OAuthError(f"Could not read the Google account's address: {exc}") from exc
    return (response.json().get("email") or "").strip().lower()


def xoauth2_string(address: str, access_token: str) -> str:
    """The SASL XOAUTH2 blob both IMAP and SMTP authenticate with."""
    return f"user={address}\x01auth=Bearer {access_token}\x01\x01"
