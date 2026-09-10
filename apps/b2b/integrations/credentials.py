"""The one Facebook app every workspace connects through.

`META_APP_ID` / `META_APP_SECRET` in the settings are **Weel's own** app, and
it is the only one. A company never types an app id, a secret or a token: the
owner presses "Ulash", signs in to Facebook as themselves, picks their pages on
Meta's own screen, and the token Meta issues is stored against *their*
`company_id`. A thousand workspaces are a thousand rows in `b2b_integration`,
not a thousand entries in a `.env` — the same shape as the Gmail connection
(`B2B_MAIL_GOOGLE_CLIENT_ID` is one value; each employee's refresh token lives
in `b2b_mail_account`).

There used to be a second path — a workspace pasting in an app of its own.
It was removed on 2026-09-10: it asked people for credentials, and one app is
what makes the webhook's signature a single, checkable fact.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class MetaCredentials:
    """Weel's Facebook app, as everything downstream needs it."""

    app_id: str
    app_secret: str
    redirect_uri: str

    #: The string Meta quotes back once, when the webhook is configured.
    verify_token: str

    @property
    def is_complete(self) -> bool:
        return bool(self.app_id and self.app_secret and self.redirect_uri)


def global_credentials() -> MetaCredentials:
    """From the settings. Possibly empty — a deployment that has not set the
    app up is a real state, and [MetaCredentials.is_complete] is how the views
    ask about it rather than an exception nobody can act on."""
    return MetaCredentials(
        app_id=(getattr(settings, "META_APP_ID", "") or "").strip(),
        app_secret=(getattr(settings, "META_APP_SECRET", "") or "").strip(),
        redirect_uri=(getattr(settings, "META_REDIRECT_URI", "") or "").strip(),
        verify_token=(
            getattr(settings, "META_WEBHOOK_VERIFY_TOKEN", "") or ""
        ).strip(),
    )


def is_available() -> bool:
    """Whether a connection can be offered at all."""
    if not getattr(settings, "META_INTEGRATION_ENABLED", False):
        return False
    return global_credentials().is_complete
