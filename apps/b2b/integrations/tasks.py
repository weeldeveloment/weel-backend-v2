"""Background work for connected services.

Three jobs, and the reason each is off the request:

* [notify_meta_lead]   — a push costs a call to Firebase. Meta is waiting for
  a 200 on the webhook and has a short patience for it; a slow answer is a
  redelivery.
* [sync_meta_pages]    — the catch-up pass. Webhooks are how leads arrive, and
  this is what covers the deliveries that never did.
* [refresh_meta_tokens] — a long-lived user token lasts about sixty days. This
  notices one about to run out and marks the connection so the screen can ask
  for a reconnect *before* the leads stop instead of after.
"""
from __future__ import annotations

import logging

from core.celery import app
from django.utils import timezone

from apps.b2b.integrations import credentials
from apps.b2b.integrations import ingest
from apps.b2b.integrations import repository as int_repo
from apps.b2b.models import IntegrationStatus
from apps.b2b.workspace import push_text
from apps.b2b.workspace import repository as repo

logger = logging.getLogger(__name__)

#: How close to expiry a token has to be before the workspace is warned.
TOKEN_WARNING_DAYS = 7


@app.task(name="b2b.integrations.notify_meta_lead")
def notify_meta_lead(lead_id: int, company_id: int, body: str) -> int:
    """The same announcement a manager's posted lead makes.

    A Meta lead is unclaimed and belongs to nobody, so it goes to the whole
    roster — including the people with no push token, who get the feed row and
    find it when they next open the app.
    """
    recipients = repo.list_company_recipients(company_id)
    if not recipients:
        return 0

    from apps.b2b.mail.repository import create_notification

    for recipient in recipients:
        try:
            create_notification(
                company_id=recipient["company_id"],
                employee_id=recipient["employee_id"],
                kind="lead",
                title=push_text.META_LEAD_TITLE,
                body=body,
                payload={"lead_id": lead_id, "source": "meta"},
            )
        except Exception:  # noqa: BLE001 — the lead itself is stored
            logger.exception(
                "Could not record the Meta-lead notification for employee %s",
                recipient["employee_id"],
            )

    tokens = [r["fcm_token"] for r in recipients if r.get("fcm_token")]
    if not tokens:
        return 0
    try:
        from apps.notification.service import (
            B2B_ANDROID_CHANNEL,
            FCMService,
            b2b_firebase_app,
        )

        FCMService.send_to_tokens(
            tokens=tokens,
            title=push_text.META_LEAD_TITLE,
            body=body,
            data={"type": "lead", "lead_id": str(lead_id), "source": "meta"},
            app=b2b_firebase_app(),
            android_channel_id=B2B_ANDROID_CHANNEL,
            deactivate_invalid=repo.clear_employee_fcm_tokens,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to push the Meta lead %s", lead_id)
    return len(tokens)


@app.task(name="b2b.integrations.ingest_meta_lead")
def ingest_meta_lead(page_row_id: int, leadgen_id: str, form_id: str = "",
                     event_id: int | None = None) -> int | None:
    """One webhook delivery, handled off the request.

    Meta wants its 200 in seconds and this makes two Graph calls, so the view
    answers immediately and the work lands here. `event_id` is the row the
    view already reserved; failing in a way that might not repeat gives it
    back, so Meta's own retry gets another go.
    """
    page = int_repo.find_page_by_row_id(page_row_id)
    if not page:
        if event_id:
            int_repo.finish_event(event_id, status="failed", error="Page is gone")
        return None

    try:
        lead = ingest.ingest_lead(page, leadgen_id, form_id=form_id)
    except ingest.IngestError as exc:
        logger.warning("Meta lead %s not ingested: %s", leadgen_id, exc)
        if event_id:
            if exc.retryable:
                int_repo.release_event(event_id)
            else:
                int_repo.finish_event(event_id, status="failed", error=str(exc))
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Meta lead %s blew up", leadgen_id)
        if event_id:
            int_repo.release_event(event_id)
        raise

    if event_id:
        int_repo.finish_event(
            event_id,
            status="stored" if lead else "skipped",
            lead_id=(lead or {}).get("id"),
        )
    return (lead or {}).get("id")


@app.task(name="b2b.integrations.sync_meta_pages")
def sync_meta_pages(company_id: int | None = None) -> int:
    """Pull recent submissions for every active page. Also the "Sinxronlash"
    button, which passes one company."""
    total = 0
    for page in int_repo.list_active_pages(company_id):
        # Asked per page rather than once: with workspaces bringing their own
        # Facebook apps, "is Meta configured" has a different answer for each
        # company on the same server.
        if not credentials.is_available(page["company_id"]):
            continue
        try:
            total += ingest.sync_page(page)
        except Exception:  # noqa: BLE001 — one bad page must not stop the rest
            logger.exception("Meta sync failed for page %s", page.get("page_id"))
        integration = int_repo.get_integration_by_id(page["integration_id"])
        if integration:
            int_repo.mark_synced(integration["id"])
    return total


@app.task(name="b2b.integrations.refresh_meta_tokens")
def refresh_meta_tokens() -> int:
    """Warn about a user token nearing its end.

    Meta has no refresh grant for this: a long-lived token is extended by the
    person signing in again, which is a thing only they can do. So this does
    not renew anything — it marks the connection as needing attention while
    there is still a week to act, because the alternative is finding out when
    a customer's enquiry silently fails to arrive.
    """
    from apps.b2b.models import IntegrationProvider
    from shared.raw.db import fetch_all
    from apps.b2b.raw.tables import B2B_INTEGRATION_TABLE

    deadline = timezone.now() + timezone.timedelta(days=TOKEN_WARNING_DAYS)
    rows = fetch_all(
        f"SELECT * FROM {B2B_INTEGRATION_TABLE} "
        f"WHERE provider = %s AND status = %s AND token_expires_at IS NOT NULL "
        f"AND token_expires_at < %s",
        [IntegrationProvider.META, IntegrationStatus.CONNECTED, deadline],
    )
    for row in rows:
        int_repo.set_integration_status(
            row["id"],
            IntegrationStatus.ERROR,
            error="Meta ruxsati tugayapti — hisobni qayta ulang.",
        )
    return len(rows)
