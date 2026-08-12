"""Opens an authenticated IMAP or SMTP connection to a connected account.

The two ways an account can be connected — an app password or a Google
refresh token — differ only in how the login step is performed. Putting that
difference here means ``imap_sync`` and ``smtp_send`` never branch on it, and
adding a third provider later touches one file.

Access tokens are refreshed lazily: they last an hour, a sync runs every
minute, and refreshing on every pass would be a wasted round trip to Google
fifty-nine times out of sixty.
"""
from __future__ import annotations

import imaplib
import logging
import smtplib

from django.utils import timezone

from apps.b2b.mail import crypto, oauth, repository as repo

logger = logging.getLogger(__name__)

# Refreshed a little before it actually expires, so a long sync cannot have the
# token die underneath it mid-run.
_REFRESH_MARGIN = timezone.timedelta(minutes=5)


class MailAuthError(RuntimeError):
    """The account's stored credential no longer works.

    Always permanent: an app password that was revoked or a Google grant the
    user removed cannot be retried into working. Callers mark the account and
    ask the person to reconnect.
    """


def _access_token(account: dict) -> str:
    """A valid Google access token, refreshing it if needed."""
    expires_at = account.get("oauth_expires_at")
    cached = account.get("oauth_access_enc")

    if cached and expires_at and expires_at - _REFRESH_MARGIN > timezone.now():
        return crypto.decrypt(cached)

    try:
        refreshed = oauth.refresh_access_token(crypto.decrypt(account["secret_enc"]))
    except oauth.OAuthError as exc:
        raise MailAuthError(str(exc)) from exc
    except ValueError as exc:
        raise MailAuthError(f"Stored credential could not be read: {exc}") from exc

    repo.update_account(
        account["id"],
        oauth_access_enc=crypto.encrypt(refreshed["access_token"]),
        oauth_expires_at=refreshed["expires_at"],
    )
    # Kept in sync in memory too, so a caller opening IMAP and then SMTP does
    # not refresh twice.
    account["oauth_access_enc"] = crypto.encrypt(refreshed["access_token"])
    account["oauth_expires_at"] = refreshed["expires_at"]
    return refreshed["access_token"]


def open_imap(account: dict) -> imaplib.IMAP4_SSL:
    host = account["imap_host"]
    port = account.get("imap_port") or 993
    if not host:
        raise MailAuthError("This account has no IMAP server configured.")

    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=30)
    except OSError as exc:
        # Not an auth problem — the server is unreachable, which is worth
        # retrying rather than telling the user to reconnect.
        raise ConnectionError(f"Could not reach {host}:{port}: {exc}") from exc

    try:
        if account["auth_type"] == "oauth":
            token = _access_token(account)
            client.authenticate(
                "XOAUTH2",
                lambda _: oauth.xoauth2_string(account["address"], token).encode(),
            )
        else:
            client.login(account["address"], crypto.decrypt(account["secret_enc"]))
    except imaplib.IMAP4.error as exc:
        client.logout()
        raise MailAuthError(f"{account['address']} was refused by {host}: {exc}") from exc
    except ValueError as exc:
        client.logout()
        raise MailAuthError(f"Stored credential could not be read: {exc}") from exc

    return client


def open_smtp(account: dict) -> smtplib.SMTP | smtplib.SMTP_SSL:
    host = account["smtp_host"]
    port = account.get("smtp_port") or 587
    if not host:
        raise MailAuthError("This account has no SMTP server configured.")

    try:
        # 465 is implicit TLS from the first byte; 587 starts plain and is
        # upgraded. Providers differ, so both are supported.
        if port == 465:
            client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30)
            client.ehlo()
        else:
            client = smtplib.SMTP(host, port, timeout=30)
            client.ehlo()
            client.starttls()
            client.ehlo()
    except OSError as exc:
        raise ConnectionError(f"Could not reach {host}:{port}: {exc}") from exc

    try:
        if account["auth_type"] == "oauth":
            token = _access_token(account)
            client.auth(
                "XOAUTH2",
                lambda _=None: oauth.xoauth2_string(account["address"], token),
                initial_response_ok=True,
            )
        else:
            client.login(account["address"], crypto.decrypt(account["secret_enc"]))
    except smtplib.SMTPAuthenticationError as exc:
        client.quit()
        raise MailAuthError(f"{account['address']} was refused by {host}: {exc}") from exc
    except ValueError as exc:
        client.quit()
        raise MailAuthError(f"Stored credential could not be read: {exc}") from exc

    return client


def verify(account: dict) -> None:
    """Prove the credential works, before an account is saved.

    Both directions are checked: an app password that reads mail but cannot
    send is a real Gmail configuration, and finding out at send time would
    look like the message vanished.
    """
    imap = open_imap(account)
    try:
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise MailAuthError(f"{account['address']} has no readable INBOX.")
    finally:
        try:
            imap.logout()
        except OSError:
            pass

    smtp = open_smtp(account)
    try:
        smtp.noop()
    finally:
        try:
            smtp.quit()
        except OSError:
            pass
