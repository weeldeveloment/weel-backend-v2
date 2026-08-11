"""Checks a customer's domain is pointed at us before we let mail flow.

A company owns ``kompaniya.com`` and its DNS; we can only tell it which records
to publish and then look. All four matter, and for different reasons:

* **MX**    — without it nobody can send *to* the company at all.
* **SPF**   — without it Gmail sees our relay sending as their domain and, since
  2024, may reject outright rather than just flag.
* **DKIM**  — the signature that survives forwarding, which SPF does not.
* **DMARC** — the policy that makes the other two enforceable, and the only one
  a receiving server reads to decide what to do on failure.

Publishing DNS takes minutes to hours to propagate, so nothing here is fatal:
each check reports independently and the settings screen shows which are still
outstanding.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Public resolvers rather than the container's. A recursive resolver caches a
# negative answer from before the customer published, and we would then keep
# telling them their correct records are missing until that TTL expired.
_NAMESERVERS = ["1.1.1.1", "8.8.8.8"]
_TIMEOUT = 5.0


def _resolver():
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = _NAMESERVERS
    resolver.lifetime = _TIMEOUT
    resolver.timeout = _TIMEOUT
    return resolver


def _query(name: str, record_type: str) -> list[str]:
    import dns.resolver

    try:
        answers = _resolver().resolve(name, record_type)
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
    ):
        return []
    except Exception:  # noqa: BLE001 - a resolver bug must not fail the request
        logger.exception("DNS lookup failed for %s %s", name, record_type)
        return []

    out: list[str] = []
    for answer in answers:
        if record_type == "MX":
            out.append(str(answer.exchange).rstrip(".").lower())
        elif record_type == "TXT":
            # dnspython splits long TXT values into 255-byte chunks exactly as
            # the wire format does; a 2048-bit DKIM key always spans several.
            out.append(b"".join(answer.strings).decode("utf-8", "replace"))
        else:
            out.append(str(answer).rstrip(".").lower())
    return out


def expected_records(domain: str, dkim_selector: str, dkim_record: str | None) -> list[dict]:
    """What the customer has to publish, shaped for display in the dashboard."""
    mx_host = getattr(settings, "B2B_MAIL_MX_HOST", "") or "mail.weel.uz"
    spf_include = getattr(settings, "B2B_MAIL_SPF_INCLUDE", "") or f"mx:{mx_host}"

    records = [
        {
            "kind": "MX",
            "host": "@",
            "value": mx_host,
            "priority": 10,
            "note": "Kompaniyaga kelgan xatlar shu serverga tushadi.",
        },
        {
            "kind": "TXT",
            "host": "@",
            "value": f"v=spf1 {spf_include} ~all",
            "note": "Bu serverga sizning domeningizdan xat yuborishga ruxsat beradi.",
        },
    ]
    if dkim_record:
        records.append({
            "kind": "TXT",
            "host": f"{dkim_selector}._domainkey",
            "value": dkim_record,
            "note": "Har bir xatni imzolaydi — Gmail spamga tushirmasligi uchun.",
        })
    records.append({
        "kind": "TXT",
        "host": "_dmarc",
        "value": f"v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}",
        "note": "Imzosi to'g'ri kelmagan xatlarga nima qilishni belgilaydi.",
    })
    return records


def check_domain(domain: str, dkim_selector: str) -> dict:
    """Look up all four records. Every key is a plain bool plus a detail string."""
    mx_host = (getattr(settings, "B2B_MAIL_MX_HOST", "") or "").lower()
    spf_include = getattr(settings, "B2B_MAIL_SPF_INCLUDE", "") or ""

    mx_hosts = _query(domain, "MX")
    mx_ok = bool(mx_hosts) and (not mx_host or mx_host in mx_hosts)

    txt = _query(domain, "TXT")
    spf_values = [value for value in txt if value.lower().startswith("v=spf1")]
    # An SPF record that exists but does not authorise us is worse than none at
    # all — it is an explicit "this sender is not allowed" — so match on the
    # include, not merely on the record's presence.
    spf_ok = bool(spf_values) and (
        not spf_include or any(spf_include.lower() in value.lower() for value in spf_values)
    )

    dkim_values = _query(f"{dkim_selector}._domainkey.{domain}", "TXT")
    # A record that says v=DKIM1 but carries no `p=` is how a key is *revoked*,
    # so the public key part has to be there too.
    dkim_ok = any(
        value.lower().startswith("v=dkim1") and "p=" in value and not value.rstrip().endswith("p=")
        for value in dkim_values
    )

    dmarc_values = _query(f"_dmarc.{domain}", "TXT")
    dmarc_ok = any(value.lower().startswith("v=dmarc1") for value in dmarc_values)

    return {
        "mx_ok": mx_ok,
        "spf_ok": spf_ok,
        "dkim_ok": dkim_ok,
        "dmarc_ok": dmarc_ok,
        "found": {
            "mx": mx_hosts,
            "spf": spf_values,
            "dkim": dkim_values,
            "dmarc": dmarc_values,
        },
    }
