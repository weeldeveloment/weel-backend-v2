"""Known mail providers, so connecting an inbox is address + password.

Asking somebody for an IMAP host, a port and an encryption mode is how a
connect screen loses most of the people who open it. Almost every address we
will see is one of a handful of providers, and their settings are public and
stable — so the domain is looked up here and the fields are filled in.

Anything unrecognised falls back to the near-universal convention
(`imap.<domain>` / `smtp.<domain>`, 993/587), which the "test connection" step
then proves or disproves before the account is saved. A provider we guessed
wrong is a bad error message, not a broken account.
"""
from __future__ import annotations

from typing import NamedTuple


class ProviderSettings(NamedTuple):
    key: str
    label: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    # Whether an ordinary account password will be refused. Gmail and Mail.ru
    # require a generated app password, and saying so up front saves the user
    # a failed attempt they would read as "the app is broken".
    requires_app_password: bool
    help_url: str | None = None


GMAIL = ProviderSettings(
    key="gmail",
    label="Gmail",
    imap_host="imap.gmail.com",
    imap_port=993,
    smtp_host="smtp.gmail.com",
    smtp_port=587,
    requires_app_password=True,
    help_url="https://myaccount.google.com/apppasswords",
)

_PROVIDERS: dict[str, ProviderSettings] = {
    "gmail.com": GMAIL,
    "googlemail.com": GMAIL,
    "yandex.ru": ProviderSettings(
        key="yandex",
        label="Yandex",
        imap_host="imap.yandex.ru",
        imap_port=993,
        smtp_host="smtp.yandex.ru",
        smtp_port=465,
        requires_app_password=True,
        help_url="https://id.yandex.ru/security/app-passwords",
    ),
    "mail.ru": ProviderSettings(
        key="mailru",
        label="Mail.ru",
        imap_host="imap.mail.ru",
        imap_port=993,
        smtp_host="smtp.mail.ru",
        smtp_port=465,
        requires_app_password=True,
        help_url="https://account.mail.ru/user/2-step-auth/passwords/",
    ),
    "outlook.com": ProviderSettings(
        key="outlook",
        label="Outlook",
        imap_host="outlook.office365.com",
        imap_port=993,
        smtp_host="smtp.office365.com",
        smtp_port=587,
        requires_app_password=True,
        help_url="https://account.microsoft.com/security",
    ),
    "umail.uz": ProviderSettings(
        key="umail",
        label="UMAIL",
        imap_host="imap.umail.uz",
        imap_port=993,
        smtp_host="smtp.umail.uz",
        smtp_port=465,
        requires_app_password=False,
    ),
}

# Aliases that share a provider's servers.
_PROVIDERS["hotmail.com"] = _PROVIDERS["outlook.com"]
_PROVIDERS["live.com"] = _PROVIDERS["outlook.com"]
_PROVIDERS["yandex.com"] = _PROVIDERS["yandex.ru"]
_PROVIDERS["bk.ru"] = _PROVIDERS["mail.ru"]
_PROVIDERS["inbox.ru"] = _PROVIDERS["mail.ru"]
_PROVIDERS["list.ru"] = _PROVIDERS["mail.ru"]


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().lower() if "@" in address else ""


def for_address(address: str) -> ProviderSettings:
    """Settings for an address, guessed from its domain if unknown."""
    domain = domain_of(address)
    known = _PROVIDERS.get(domain)
    if known:
        return known

    return ProviderSettings(
        key="imap",
        label=domain or "IMAP",
        imap_host=f"imap.{domain}" if domain else "",
        imap_port=993,
        smtp_host=f"smtp.{domain}" if domain else "",
        smtp_port=587,
        requires_app_password=False,
    )


def is_gmail(address: str) -> bool:
    """Whether this address could instead be connected with Google sign-in."""
    return domain_of(address) in ("gmail.com", "googlemail.com")


def describe(address: str) -> dict:
    """What the connect screen shows before anything has been typed."""
    settings = for_address(address)
    return {
        "provider": settings.key,
        "label": settings.label,
        "imap_host": settings.imap_host,
        "imap_port": settings.imap_port,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "requires_app_password": settings.requires_app_password,
        "help_url": settings.help_url,
        "supports_oauth": is_gmail(address),
    }
