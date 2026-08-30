"""The Meta Graph API, as much of it as lead ads need.

Five calls and one signature check:

* [authorize_url] / [exchange_code] — the login, which is plain OAuth 2.
  Both take the [MetaCredentials] to sign in *through*, because a workspace
  may connect with its own Facebook app rather than ours — see
  `credentials.py`. Nothing here reads the settings; that decision is made in
  one place and handed down.
* [long_lived_token]  — a login token lasts about an hour; this trades it for
  one that lasts about sixty days, which is what gets stored.
* [list_pages]        — the pages the person administers, each with its own
  page token. A *page* token minted from a long-lived user token does not
  expire, so it is the one the ingest path actually uses.
* [subscribe_page]    — tells Meta to post this app's webhook when a form on
  that page is filled in. Without it nothing ever arrives.
* [fetch_lead]        — one `leadgen_id` → the answers the customer typed.
* [verify_signature]  — that a webhook really came from Meta.

Everything raises [MetaError] with a message fit to show somebody: the screen
that calls these is an owner connecting an account, and "Meta refused: the
page needs leads_retrieval" is actionable where "400" is not.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import urlencode

import requests
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Pinned rather than "latest". Meta retires a version roughly every two
#: years and changes field shapes between them; an unpinned call starts
#: failing on their schedule instead of ours.
API_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{API_VERSION}"
DIALOG = f"https://www.facebook.com/{API_VERSION}/dialog/oauth"

#: What we ask the person to grant, and why:
#:
#: * `pages_show_list`        — to list the pages they administer at all.
#: * `pages_manage_metadata`  — to subscribe the page to the leadgen webhook.
#: * `leads_retrieval`        — to read the answers on a submitted form.
#: * `pages_read_engagement`  — to read the page's own name.
#:
#: `leads_retrieval` and `pages_manage_metadata` need App Review before the
#: app works for anyone outside its own testers; see apps/b2b/integrations/README.md.
SCOPES = [
    "pages_show_list",
    "pages_manage_metadata",
    "pages_read_engagement",
    "leads_retrieval",
]

TIMEOUT = 20


class MetaError(RuntimeError):
    """Something Meta refused, in words worth showing the person connecting."""


def _check(creds) -> None:
    """That the app we are about to call through is actually configured."""
    if not creds or not creds.is_complete:
        raise ImproperlyConfigured(
            "No usable Meta app. Either set META_APP_ID, META_APP_SECRET and "
            "META_REDIRECT_URI, or give the workspace its own app — see "
            "apps/b2b/integrations/credentials.py."
        )


# ─── HTTP ─────────────────────────────────────────────────────────────────────

def _request(method: str, path: str, **kwargs) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    try:
        response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise MetaError(f"Meta bilan bog‘lanib bo‘lmadi: {exc}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400 or "error" in payload:
        error = payload.get("error") or {}
        message = error.get("message") or response.text[:300] or "noma’lum xato"
        # Meta's own subcode is the only thing that distinguishes "the user
        # removed our app" from "you asked for a field you cannot have", and
        # the first is the one an owner has to act on.
        raise MetaError(message)
    return payload


# ─── OAuth ────────────────────────────────────────────────────────────────────

def authorize_url(state: str, creds) -> str:
    """Where to send the browser. ``state`` ties the callback back to a company."""
    _check(creds)
    return f"{DIALOG}?" + urlencode({
        "client_id": creds.app_id,
        "redirect_uri": creds.redirect_uri,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
        # Somebody reconnecting after removing a page would otherwise be sent
        # straight back with the old, narrower grant and no way to widen it.
        "auth_type": "rerequest",
    })


def exchange_code(code: str, creds) -> dict[str, Any]:
    """The callback's code → a short-lived user token."""
    _check(creds)
    payload = _request("GET", "/oauth/access_token", params={
        "client_id": creds.app_id,
        "client_secret": creds.app_secret,
        "redirect_uri": creds.redirect_uri,
        "code": code,
    })
    token = payload.get("access_token")
    if not token:
        raise MetaError("Meta token qaytarmadi.")
    return {
        "access_token": token,
        "expires_at": _expiry(payload.get("expires_in")),
    }


def long_lived_token(short_token: str, creds) -> dict[str, Any]:
    """~1 hour → ~60 days. The one that gets stored."""
    _check(creds)
    payload = _request("GET", "/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": creds.app_id,
        "client_secret": creds.app_secret,
        "fb_exchange_token": short_token,
    })
    token = payload.get("access_token")
    if not token:
        raise MetaError("Meta uzoq muddatli token qaytarmadi.")
    return {
        "access_token": token,
        "expires_at": _expiry(payload.get("expires_in")),
    }


def _expiry(seconds: Any):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    return timezone.now() + timezone.timedelta(seconds=seconds)


# ─── Account and pages ────────────────────────────────────────────────────────

def me(access_token: str) -> dict[str, Any]:
    """Whose account this is — for the "ulangan hisob" line on the screen."""
    return _request("GET", "/me", params={
        "fields": "id,name", "access_token": access_token,
    })


def list_pages(access_token: str) -> list[dict[str, Any]]:
    """Every page the person administers, each with its own page token.

    Paged: an agency account can administer more than the twenty-five Meta
    returns by default, and stopping at the first page would silently connect
    some of somebody's pages and not others.
    """
    pages: list[dict[str, Any]] = []
    payload = _request("GET", "/me/accounts", params={
        "fields": "id,name,access_token,tasks",
        "limit": 100,
        "access_token": access_token,
    })
    while True:
        pages.extend(payload.get("data") or [])
        nxt = ((payload.get("paging") or {}).get("next")) or ""
        if not nxt or len(pages) >= 500:
            break
        payload = _request("GET", nxt)
    return pages


def subscribe_page(page_id: str, page_token: str) -> None:
    """Ask Meta to post our webhook when a form on this page is submitted.

    Without this the app is authorised and nothing ever arrives, which is the
    single most common way a lead-ads integration is "connected" and dead.
    """
    _request("POST", f"/{page_id}/subscribed_apps", params={
        "subscribed_fields": "leadgen",
        "access_token": page_token,
    })


def unsubscribe_page(page_id: str, page_token: str) -> None:
    _request("DELETE", f"/{page_id}/subscribed_apps", params={
        "access_token": page_token,
    })


# ─── Leads ────────────────────────────────────────────────────────────────────

#: What one submitted form looks like coming back. `field_data` is the answers.
LEAD_FIELDS = "id,created_time,ad_id,ad_name,form_id,campaign_name,platform,field_data"


def fetch_lead(leadgen_id: str, page_token: str) -> dict[str, Any]:
    return _request("GET", f"/{leadgen_id}", params={
        "fields": LEAD_FIELDS, "access_token": page_token,
    })


def form_name(form_id: str, page_token: str) -> str:
    """The marketer's own name for the form — printed on the card.

    Best-effort: a lead is worth having even when we cannot name the form it
    came from, so this answers "" rather than raising.
    """
    try:
        payload = _request("GET", f"/{form_id}", params={
            "fields": "name", "access_token": page_token,
        })
    except MetaError:
        logger.info("Could not read the name of Meta form %s", form_id)
        return ""
    return (payload.get("name") or "").strip()


def recent_leads(form_id: str, page_token: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """A form's latest submissions — the catch-up path.

    Webhooks are the way leads arrive; this is what fills the gap when one was
    missed (our server was down, the subscription was added late) and what the
    "Sinxronlash" button on the screen runs.
    """
    payload = _request("GET", f"/{form_id}/leads", params={
        "fields": LEAD_FIELDS, "limit": min(limit, 100), "access_token": page_token,
    })
    return payload.get("data") or []


def list_forms(page_id: str, page_token: str) -> list[dict[str, Any]]:
    payload = _request("GET", f"/{page_id}/leadgen_forms", params={
        "fields": "id,name,status", "limit": 100, "access_token": page_token,
    })
    return payload.get("data") or []


# ─── Webhook authenticity ─────────────────────────────────────────────────────

def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """Whether this request body was signed with the given app's secret.

    Meta sends `X-Hub-Signature-256: sha256=<hex>`. Anyone who learns the
    webhook URL can post to it otherwise, and the ingest path raises leads on
    somebody's sales board — so an unsigned or wrongly signed delivery is
    dropped, not logged and processed.

    The secret is passed in rather than read here because one webhook URL now
    receives deliveries from several apps: ours, and every workspace that
    connected through its own. Which secret to check against is decided by the
    *page* the delivery names — see `MetaWebhookView`.
    """
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode(), msg=body, digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1].strip())
