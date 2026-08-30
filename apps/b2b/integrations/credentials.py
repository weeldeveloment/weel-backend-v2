"""Whose Meta app a company connects through.

Two answers, and the whole point of this module is that nothing else has to
know which one it got:

* **Ours** — `META_APP_ID` / `META_APP_SECRET` in the settings. One Facebook
  app serving every customer, which is how OAuth is normally deployed: the
  *app* is ours, the *account* is theirs, and their token lives in
  `b2b_integration` under their own `company_id`. A thousand workspaces do not
  need a thousand entries in a `.env`.

* **Theirs** — `b2b_integration.app_id` / `app_secret_enc`. For the company
  that cannot use ours: while our app is still in Meta's review only its
  listed testers can authorise it, and some customers will not let their
  advertising data pass through an app they do not own.

The company's own app wins when it is complete, ours is the fallback, and a
deployment can offer either, both, or neither. [for_company] is the only place
that decision is made — everywhere else takes a [MetaCredentials] and uses it.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from django.conf import settings

from apps.b2b.integrations import crypto
from apps.b2b.integrations import repository as int_repo
from apps.b2b.models import IntegrationProvider


@dataclass(frozen=True)
class MetaCredentials:
    """One Facebook app, as everything downstream needs it."""

    app_id: str
    app_secret: str
    redirect_uri: str

    #: The string Meta quotes back when the webhook is configured.
    verify_token: str

    #: Whether this is the company's own app rather than ours. Reported to the
    #: screen, which has to tell the owner *which* app's settings to paste the
    #: redirect URI into — pointing them at the wrong Facebook app is the
    #: single most confusing way this can be got wrong.
    is_own: bool = False

    @property
    def is_complete(self) -> bool:
        return bool(self.app_id and self.app_secret and self.redirect_uri)


def _redirect_uri() -> str:
    return (getattr(settings, "META_REDIRECT_URI", "") or "").strip()


def global_credentials() -> MetaCredentials:
    """Ours, from the settings. Possibly empty — a deployment that has not set
    one up is a real state, and [MetaCredentials.is_complete] is how the views
    ask about it rather than an exception nobody can act on."""
    return MetaCredentials(
        app_id=(getattr(settings, "META_APP_ID", "") or "").strip(),
        app_secret=(getattr(settings, "META_APP_SECRET", "") or "").strip(),
        redirect_uri=_redirect_uri(),
        verify_token=(
            getattr(settings, "META_WEBHOOK_VERIFY_TOKEN", "") or ""
        ).strip(),
        is_own=False,
    )


def from_integration(integration: dict | None) -> MetaCredentials | None:
    """The company's own app, if the row carries a complete one.

    Incomplete is treated as absent rather than as an error: a half-saved app
    should fall back to ours and keep the leads flowing, not break the
    connection while somebody works out which half is missing.
    """
    if not integration:
        return None
    app_id = (integration.get("app_id") or "").strip()
    encrypted = integration.get("app_secret_enc")
    if not app_id or not encrypted:
        return None
    try:
        app_secret = crypto.decrypt(encrypted)
    except ValueError:
        # Encrypted under a key that is no longer configured. Falling back to
        # ours would quietly connect them through the wrong app, so this one
        # answers "no usable app" and the screen asks them to enter it again.
        return None
    return MetaCredentials(
        app_id=app_id,
        app_secret=app_secret,
        redirect_uri=_redirect_uri(),
        verify_token=(integration.get("webhook_verify_token") or "").strip(),
        is_own=True,
    )


def for_company(company_id: int) -> MetaCredentials:
    """Which app this workspace connects through. Theirs if they have one."""
    integration = int_repo.get_integration(company_id, IntegrationProvider.META)
    return from_integration(integration) or global_credentials()


def is_available(company_id: int | None = None) -> bool:
    """Whether a connection can be offered at all.

    A workspace with its own app can connect on a deployment that has none of
    its own, which is the whole point of the second path — so this asks about
    the company when it is given one.
    """
    if not getattr(settings, "META_INTEGRATION_ENABLED", False):
        return False
    if company_id is not None and for_company(company_id).is_complete:
        return True
    return global_credentials().is_complete


def new_verify_token() -> str:
    """A webhook verify token nobody has to invent.

    Generated rather than typed: it is a shared secret between Meta and this
    server, and the ones people choose are `12345` and the company's name.
    """
    return secrets.token_urlsafe(24)
