"""One Meta lead → one card on the sales board.

The whole difficulty here is that a lead-ad form is whatever the marketer drew.
Meta guarantees the *shape* — a list of `{name, values}` — and nothing about
the names: a form asks `full_name` or `ism_familiya` or "Ismingiz", and the
funnel needs a person, a phone and something they want to buy. [_map_fields]
is that translation, and everything it cannot place is kept verbatim in
`external_data` so nothing the customer typed is lost.

The lead lands **unclaimed**, exactly like one a manager posts to the board:
Meta does not know who in the company should call, and picking somebody would
put a deal in one person's list that nobody agreed to. The whole roster is
notified, and the first to take it owns it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from apps.b2b.models import (
    IntegrationProvider,
    IntegrationStatus,
    LeadActivityKind,
    LeadSource,
)
from apps.b2b.integrations import crypto, meta
from apps.b2b.integrations import repository as int_repo
from apps.b2b.workspace import repository as repo

logger = logging.getLogger(__name__)


class IngestError(RuntimeError):
    """The lead could not be turned into a card. Carries whether retrying helps."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# ─── Field names ──────────────────────────────────────────────────────────────
#
# Meta's own question types come back under fixed names; anything the marketer
# wrote themselves comes back under a slug of their question. Both are matched,
# the fixed names first, so a form that happens to ask "Telefon raqamingiz" as
# a custom question still fills the phone column.

_FULL_NAME = ("full_name", "name", "ism", "ismingiz", "fio", "ism_familiya")
_FIRST_NAME = ("first_name", "ism_first", "given_name")
_LAST_NAME = ("last_name", "familiya", "surname", "family_name")
_PHONE = ("phone_number", "phone", "telefon", "telefon_raqami",
          "telefon_raqamingiz", "raqam", "mobile", "whatsapp_number")
_EMAIL = ("email", "email_address", "pochta", "elektron_pochta")
_COMPANY = ("company_name", "company", "kompaniya", "kompaniya_nomi",
            "tashkilot", "biznes")
_POSITION = ("job_title", "position", "lavozim")
_ADDRESS = ("street_address", "address", "city", "manzil", "shahar")
_PRODUCT = ("product", "mahsulot", "xizmat", "service", "interested_in",
            "qiziqish", "qaysi_xizmat")


def _slug(name: str) -> str:
    """A question's name, comparable across the two ways Meta writes them."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def _map_fields(field_data: list[dict[str, Any]]) -> dict[str, Any]:
    """The answers, sorted into the columns a lead has and a bag for the rest."""
    answers: dict[str, str] = {}
    for field in field_data or []:
        if not isinstance(field, dict):
            continue
        values = field.get("values") or []
        value = ", ".join(str(v).strip() for v in values if str(v).strip())
        if not value:
            continue
        answers[_slug(field.get("name") or "")] = value

    def pick(keys) -> str:
        for key in keys:
            if answers.get(key):
                return answers[key]
        return ""

    full_name = pick(_FULL_NAME)
    if not full_name:
        full_name = " ".join(
            part for part in (pick(_FIRST_NAME), pick(_LAST_NAME)) if part
        ).strip()

    used = set()
    for group in (_FULL_NAME, _FIRST_NAME, _LAST_NAME, _PHONE, _EMAIL,
                  _COMPANY, _POSITION, _ADDRESS, _PRODUCT):
        used.update(group)

    return {
        "full_name": full_name,
        "phone": _clean_phone(pick(_PHONE)),
        "email": pick(_EMAIL),
        "company_name": pick(_COMPANY),
        "position": pick(_POSITION),
        "address": pick(_ADDRESS),
        "product": pick(_PRODUCT),
        # Everything the form asked that has nowhere to go. Shown on the card's
        # history, so the salesperson reads the customer's actual answers
        # rather than a lead stripped down to a phone number.
        "extra": {k: v for k, v in answers.items() if k not in used},
    }


def _clean_phone(raw: str) -> str:
    """Meta sends `+998901234567`; the column is 20 characters and the
    directory matches on digits. Kept as typed, trimmed of spacing."""
    phone = re.sub(r"[^\d+]", "", raw or "")
    return phone[:20]


# ─── The ingest itself ────────────────────────────────────────────────────────

def ingest_lead(page: dict[str, Any], leadgen_id: str, *, form_id: str = "") -> dict | None:
    """Fetch one submitted form from Meta and raise it on the board.

    `page` is a `b2b_integration_page` row. Raises [IngestError] on anything
    that stopped it; the caller decides whether Meta should be allowed to
    retry.
    """
    if not page.get("is_active"):
        logger.info("Page %s is switched off; dropping lead %s",
                    page.get("page_id"), leadgen_id)
        return None

    try:
        page_token = crypto.decrypt(page.get("access_token_enc"))
    except ValueError as exc:
        int_repo.set_page_error(page["id"], str(exc))
        raise IngestError(str(exc), retryable=False) from exc

    try:
        raw = meta.fetch_lead(leadgen_id, page_token)
    except meta.MetaError as exc:
        int_repo.set_page_error(page["id"], str(exc))
        integration = int_repo.get_integration_by_id(page["integration_id"])
        if integration:
            int_repo.set_integration_status(
                integration["id"], IntegrationStatus.ERROR, error=str(exc)
            )
        # Meta being unreachable is worth another go; Meta saying no is not,
        # and the difference is not in the response — an expired token and a
        # rate limit both come back as one of these. Retryable, because the
        # cheap wrong answer here is one duplicate attempt and the expensive
        # one is a lost customer.
        raise IngestError(str(exc), retryable=True) from exc

    return store_lead(page, raw, form_id=form_id or (raw.get("form_id") or ""))


def store_lead(page: dict[str, Any], raw: dict[str, Any], *, form_id: str = "") -> dict | None:
    """Turn Meta's payload into a lead. Split out so the catch-up sync, which
    already has the payload, does not fetch it a second time."""
    company_id = page["company_id"]
    leadgen_id = str(raw.get("id") or "").strip()
    if not leadgen_id:
        raise IngestError("Meta lead has no id", retryable=False)

    mapped = _map_fields(raw.get("field_data") or [])

    # A lead with neither a phone nor an email cannot be followed up, and a
    # card nobody can act on is worse than no card — it sits on the board
    # ageing into a "harakatsiz" warning about a customer who was never
    # reachable. Kept in the event log so it is visible rather than silent.
    if not mapped["phone"] and not mapped["email"]:
        raise IngestError(
            "Formada telefon ham, email ham yo‘q — lead yaratilmadi.",
            retryable=False,
        )

    # Already on the board. `create_lead`'s unique index would answer the same
    # way, but asking first keeps the "is this new?" decision in one readable
    # place — and above all stops a retried delivery from notifying the whole
    # company a second time.
    existing = repo.find_lead_by_external_id(company_id, LeadSource.META, leadgen_id)
    if existing:
        return existing

    author_id = int_repo.fallback_author(
        company_id,
        (int_repo.get_integration_by_id(page["integration_id"]) or {}).get(
            "connected_by_id"
        ),
    )
    if author_id is None:
        raise IngestError("Bu ish joyida faol xodim yo‘q.", retryable=False)

    form_name = ""
    if form_id:
        try:
            form_name = meta.form_name(form_id, crypto.decrypt(page["access_token_enc"]))
        except (ValueError, meta.MetaError):
            form_name = ""

    # What the deal is *for*. A form that asked is quoted; one that did not
    # falls back to the form's own name, which is what the marketer called the
    # campaign and is the closest thing to an answer the customer gave.
    product = mapped["product"] or form_name or "Meta lead"
    company_name = mapped["company_name"] or mapped["full_name"] or "Meta lead"

    lead = repo.create_lead(
        company_id=company_id,
        author_id=author_id,
        company_name=company_name[:300],
        contact_full_name=(mapped["full_name"] or "Noma’lum")[:300],
        contact_phone=mapped["phone"],
        contact_position=mapped["position"][:200] or None,
        contact_email=mapped["email"][:254] or None,
        contact_address=mapped["address"] or None,
        product_name=product[:300],
        quantity=1,
        source=LeadSource.META,
        integration_id=page["integration_id"],
        external_id=leadgen_id,
        external_form_name=form_name[:300] or None,
        external_data={
            "platform": raw.get("platform"),
            "ad_id": raw.get("ad_id"),
            "ad_name": raw.get("ad_name"),
            "form_id": raw.get("form_id") or form_id,
            "campaign_name": raw.get("campaign_name"),
            "created_time": raw.get("created_time"),
            "answers": mapped["extra"],
        },
        claim_for_author=False,
    )
    if not lead:
        raise IngestError("Lead saqlanmadi.", retryable=True)

    _record_arrival(lead, page, mapped, raw, form_name)
    int_repo.count_page_lead(page["id"], page["integration_id"])
    _notify(lead, company_name, product)
    return lead


def _record_arrival(lead, page, mapped, raw, form_name: str) -> None:
    """The first line of the deal's history: where it came from and what the
    customer actually answered."""
    where = form_name or (raw.get("ad_name") or "") or page.get("page_name") or "Meta"
    lines = [f"Meta’dan keldi — {where}"]
    if raw.get("platform"):
        lines.append(f"Platforma: {raw['platform']}")
    for question, answer in (mapped["extra"] or {}).items():
        lines.append(f"{question.replace('_', ' ')}: {answer}")
    try:
        repo.add_lead_activity(
            lead["id"],
            kind=LeadActivityKind.COMMENT,
            author_id=None,
            text="\n".join(lines)[:4000],
        )
    except Exception:  # noqa: BLE001 — the lead itself is stored
        logger.exception("Could not write the arrival note for lead %s", lead["id"])


def _notify(lead: dict[str, Any], company_name: str, product: str) -> None:
    """Tell the workspace there is a lead waiting, the same way a posted one does.

    Imported here rather than at module scope: this module is imported by the
    webhook, and `apps.notification` pulls in `firebase_admin`.
    """
    from apps.b2b.integrations.tasks import notify_meta_lead

    try:
        notify_meta_lead.delay(lead["id"], lead["company_id"], f"{company_name} — {product}")
    except Exception:  # noqa: BLE001 — a lead on the board beats a push
        logger.exception("Could not queue the new-lead notification for %s", lead["id"])

    try:
        from apps.b2b.workspace import realtime

        realtime.publish_company(lead["company_id"], realtime.EVENT_LEAD, action="changed")
    except Exception:  # noqa: BLE001
        logger.exception("Could not announce Meta lead %s", lead["id"])


# ─── Catch-up ─────────────────────────────────────────────────────────────────

def sync_page(page: dict[str, Any], *, limit: int = 50) -> int:
    """Pull a page's recent submissions and raise whatever is not on the board.

    The webhook is how leads arrive; this exists because a webhook that was
    never delivered leaves no trace anywhere else — the subscription was added
    after a campaign started, our server was down for an hour, Meta gave up
    retrying. Duplicates are free: the unique index on `external_id` is what
    decides, not this loop.
    """
    try:
        page_token = crypto.decrypt(page.get("access_token_enc"))
    except ValueError as exc:
        int_repo.set_page_error(page["id"], str(exc))
        return 0

    added = 0
    try:
        forms = meta.list_forms(page["page_id"], page_token)
    except meta.MetaError as exc:
        int_repo.set_page_error(page["id"], str(exc))
        return 0

    for form in forms:
        form_id = str(form.get("id") or "")
        if not form_id:
            continue
        try:
            leads = meta.recent_leads(form_id, page_token, limit=limit)
        except meta.MetaError as exc:
            logger.info("Could not read form %s: %s", form_id, exc)
            continue
        for raw in leads:
            leadgen_id = str(raw.get("id") or "")
            if not leadgen_id:
                continue
            event = int_repo.claim_event(
                provider=IntegrationProvider.META,
                external_id=leadgen_id,
                company_id=page["company_id"],
                page_id=page["page_id"],
                payload={"source": "sync", "form_id": form_id},
            )
            if event is None:
                continue  # Seen before.
            try:
                lead = store_lead(page, raw, form_id=form_id)
            except IngestError as exc:
                int_repo.finish_event(event["id"], status="failed", error=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("Meta sync failed for lead %s", leadgen_id)
                int_repo.finish_event(event["id"], status="failed", error=str(exc))
                continue
            int_repo.finish_event(
                event["id"], status="stored", lead_id=(lead or {}).get("id")
            )
            if lead:
                added += 1

    int_repo.set_page_error(page["id"], None)
    return added
