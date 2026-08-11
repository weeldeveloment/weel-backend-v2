"""Thin client over the Mailcow admin API.

Only the handful of calls provisioning needs: add a domain, read back the DKIM
key it generated, add and delete mailboxes. Mailcow's API is quirky in two ways
worth knowing before reading this file:

* It answers ``200 OK`` for failures too, with ``{"type": "danger", ...}`` in
  the body — so the status code alone proves nothing.
* Its list endpoints return a bare object when asked for one item and a list
  when asked for all, hence ``_first``.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class MailcowError(RuntimeError):
    """Mailcow refused an operation, or could not be reached."""


def _config() -> tuple[str, str]:
    url = getattr(settings, "B2B_MAILCOW_API_URL", "")
    key = getattr(settings, "B2B_MAILCOW_API_KEY", "")
    if not url or not key:
        raise ImproperlyConfigured(
            "B2B_MAILCOW_API_URL and B2B_MAILCOW_API_KEY must be set before "
            "corporate mail can provision anything."
        )
    return url, key


def _request(method: str, path: str, payload: dict | None = None) -> Any:
    base, key = _config()
    timeout = getattr(settings, "B2B_MAILCOW_TIMEOUT", 20)
    try:
        response = requests.request(
            method,
            f"{base}/api/v1/{path.lstrip('/')}",
            json=payload,
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise MailcowError(f"Mail server unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise MailcowError(f"Mail server returned {response.status_code}: {response.text[:500]}")

    try:
        body = response.json()
    except ValueError as exc:
        raise MailcowError(f"Mail server returned non-JSON: {response.text[:200]}") from exc

    # A write returns a list of result objects; `type` is "success" or
    # "danger". Anything not explicitly successful is treated as a failure so a
    # silently-ignored provisioning step cannot leave a half-made mailbox.
    for entry in body if isinstance(body, list) else [body]:
        if isinstance(entry, dict) and entry.get("type") not in (None, "success"):
            message = entry.get("msg") or entry.get("type")
            raise MailcowError(f"Mail server rejected the request: {message}")
    return body


def _first(body: Any) -> dict | None:
    if isinstance(body, list):
        return body[0] if body else None
    return body if isinstance(body, dict) else None


# ─── Domains ──────────────────────────────────────────────────────────────────

def add_domain(domain: str, *, mailbox_quota_mb: int = 2048, max_mailboxes: int = 100) -> None:
    _request("POST", "add/domain", {
        "domain": domain,
        "description": "WEEL B2B",
        "aliases": 100,
        "mailboxes": max_mailboxes,
        "defquota": mailbox_quota_mb,
        "maxquota": mailbox_quota_mb,
        # Total storage the domain may consume across all its mailboxes.
        "quota": mailbox_quota_mb * max_mailboxes,
        "active": 1,
        "rl_value": 100,
        "rl_frame": "h",
        "backupmx": 0,
    })


def delete_domain(domain: str) -> None:
    _request("POST", "delete/domain", [domain])


def domain_exists(domain: str) -> bool:
    try:
        return _first(_request("GET", f"get/domain/{domain}")) is not None
    except MailcowError:
        return False


# ─── DKIM ─────────────────────────────────────────────────────────────────────

def add_dkim(domain: str, selector: str, key_size: int = 2048) -> None:
    _request("POST", "add/dkim", {
        "domains": domain,
        "dkim_selector": selector,
        "key_size": key_size,
    })


def get_dkim(domain: str) -> dict | None:
    """The generated public key, as the customer must publish it.

    Returns ``{"selector": ..., "public_key": ..., "record": ...}`` where
    ``record`` is the full TXT value (``v=DKIM1;k=rsa;t=s;p=...``) so the UI can
    show something copy-pasteable rather than making the customer assemble it.
    """
    try:
        entry = _first(_request("GET", f"get/dkim/{domain}"))
    except MailcowError:
        return None
    if not entry or not entry.get("pubkey"):
        return None
    return {
        "selector": entry.get("dkim_selector") or entry.get("selector"),
        "public_key": entry["pubkey"],
        "record": entry.get("dkim_txt") or f"v=DKIM1;k=rsa;t=s;p={entry['pubkey']}",
    }


# ─── Mailboxes ────────────────────────────────────────────────────────────────

def add_mailbox(
    *,
    local_part: str,
    domain: str,
    password: str,
    display_name: str,
    quota_mb: int = 2048,
) -> None:
    _request("POST", "add/mailbox", {
        "local_part": local_part,
        "domain": domain,
        "name": display_name,
        "quota": quota_mb,
        "password": password,
        "password2": password,
        "active": 1,
        "force_pw_update": 0,
        # The app is the primary client and speaks IMAP; leaving SMTP on is
        # what lets someone also configure Outlook or the Gmail app.
        "tls_enforce_in": 1,
        "tls_enforce_out": 1,
    })


def set_mailbox_password(address: str, password: str) -> None:
    _request("POST", "edit/mailbox", {
        "items": [address],
        "attr": {"password": password, "password2": password},
    })


def set_mailbox_active(address: str, active: bool) -> None:
    _request("POST", "edit/mailbox", {
        "items": [address],
        "attr": {"active": 1 if active else 0},
    })


def delete_mailbox(address: str) -> None:
    _request("POST", "delete/mailbox", [address])


def mailbox_exists(address: str) -> bool:
    try:
        return _first(_request("GET", f"get/mailbox/{address}")) is not None
    except MailcowError:
        return False
