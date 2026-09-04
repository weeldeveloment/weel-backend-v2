"""The nightly pass that has Weel AI write every company's reports.

One beat entry, `b2b.workspace.analyst_reports`, in the morning
(`B2B_ANALYST_HOUR`, Asia/Tashkent). It works out which periods are due
today — `analyst.periods_due` — and writes each for every live workspace,
one company per subtask so a vendor timeout on one does not hold up the
rest. When a report is ready the people who run the company get a feed
row and a push, in Uzbek like every other push (see `push_text`).
"""
from __future__ import annotations

import logging

from core.celery import app
from django.conf import settings
from django.utils import timezone

from apps.b2b.workspace import analyst
from apps.b2b.workspace import analyst_repository as repo
from apps.b2b.workspace import push_text

logger = logging.getLogger(__name__)


@app.task(name="b2b.workspace.analyst_reports")
def analyst_reports() -> int:
    """Queues today's reports for every workspace. Returns how many."""
    if not getattr(settings, "B2B_ANALYST_ENABLED", True):
        return 0
    today = timezone.localdate()
    periods = analyst.periods_due(today)
    queued = 0
    for company_id in repo.companies_with_staff():
        for period in periods:
            analyst_report_for.delay(company_id, period)
            queued += 1
    return queued


@app.task(name="b2b.workspace.analyst_report_for")
def analyst_report_for(company_id: int, period: str) -> int | None:
    """One report for one company, and the announcement of it."""
    try:
        report = analyst.generate(company_id, period)
    except analyst.AnalystUnavailable:
        # Neither our key nor theirs: nothing to write with. Quiet — this is
        # every workspace on a deployment that has not set the key up.
        return None
    except Exception:  # noqa: BLE001 — one company must not stop the pass
        logger.exception("Weel AI %s report failed for company %s", period, company_id)
        return None
    if not report or report.get("status") != repo.STATUS_READY:
        return report["id"] if report else None
    _announce(report)
    return report["id"]


def _announce(report: dict) -> None:
    """Feed row and push for everybody who runs the company."""
    company_id = report["company_id"]
    period = report["period"]
    title = push_text.analyst_title(period)
    # The Uzbek headline: pushes are Uzbek throughout (see `push_text`), and
    # the app opens the report in whichever language the reader set.
    body = (report.get("headline_uz") or report.get("headline_ru") or "")[:200]
    recipients = repo.manager_recipients(company_id)
    if not recipients:
        return

    from apps.b2b.mail.repository import create_notification

    for recipient in recipients:
        try:
            create_notification(
                company_id=company_id,
                employee_id=recipient["employee_id"],
                kind="analyst",
                title=title,
                body=body,
                payload={"report_id": report["id"], "period": period},
            )
        except Exception:  # noqa: BLE001 — the report itself is stored
            logger.exception("Could not record the Weel AI notification for %s",
                             recipient["employee_id"])

    tokens = [r["fcm_token"] for r in recipients if r.get("fcm_token")]
    if not tokens:
        return
    try:
        from apps.b2b.workspace.repository import (
            clear_employee_fcm_tokens,
            unread_badges_for_tokens,
        )
        from apps.notification.service import (
            B2B_ANDROID_CHANNEL,
            FCMService,
            b2b_firebase_app,
        )

        FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data={"type": "analyst", "report_id": str(report["id"]), "period": period},
            app=b2b_firebase_app(),
            android_channel_id=B2B_ANDROID_CHANNEL,
            deactivate_invalid=clear_employee_fcm_tokens,
            badge_for=unread_badges_for_tokens,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Weel AI push failed for company %s", company_id)
