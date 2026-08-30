"""Raw-SQL data access for integrations.

Same conventions as the rest of `apps/b2b`: plain dicts in and out, every
query scoped by `company_id` except the two that cannot be — a webhook
arrives addressed to a *page*, and the page is what tells us whose workspace
it belongs to.
"""
from __future__ import annotations

import json
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.models import IntegrationProvider, IntegrationStatus
from apps.b2b.raw.tables import (
    B2B_EMPLOYEE_TABLE,
    B2B_INTEGRATION_EVENT_TABLE,
    B2B_INTEGRATION_PAGE_TABLE,
    B2B_INTEGRATION_TABLE,
)


# ─── The connection ───────────────────────────────────────────────────────────

def get_integration(company_id: int, provider: str = IntegrationProvider.META):
    return fetch_one(
        f"SELECT * FROM {B2B_INTEGRATION_TABLE} "
        f"WHERE company_id = %s AND provider = %s",
        [company_id, provider],
    )


def get_integration_by_id(integration_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_INTEGRATION_TABLE} WHERE id = %s", [integration_id]
    )


def upsert_integration(
    *,
    company_id: int,
    provider: str = IntegrationProvider.META,
    account_id: str | None,
    account_name: str | None,
    access_token_enc: str | None,
    token_expires_at=None,
    scopes: str = "",
    connected_by_id: int | None,
) -> dict[str, Any] | None:
    """Connect, or reconnect.

    Reconnecting updates the row in place rather than making a second one: a
    company has one Meta connection, the pages hang off its id, and replacing
    the row would orphan every page and every lead that points at it.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_INTEGRATION_TABLE}
            (company_id, provider, status, account_id, account_name,
             access_token_enc, token_expires_at, scopes, connected_by_id,
             connected_at, last_error, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
        ON CONFLICT (company_id, provider) DO UPDATE SET
            status = EXCLUDED.status,
            account_id = EXCLUDED.account_id,
            account_name = EXCLUDED.account_name,
            access_token_enc = EXCLUDED.access_token_enc,
            token_expires_at = EXCLUDED.token_expires_at,
            scopes = EXCLUDED.scopes,
            connected_by_id = EXCLUDED.connected_by_id,
            connected_at = EXCLUDED.connected_at,
            last_error = NULL,
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, provider, IntegrationStatus.CONNECTED, account_id,
            account_name, access_token_enc, token_expires_at, scopes,
            connected_by_id, now, now, now,
        ],
    )


def set_company_app(
    *,
    company_id: int,
    provider: str = IntegrationProvider.META,
    app_id: str,
    app_secret_enc: str,
    verify_token: str,
) -> dict[str, Any] | None:
    """Give the workspace its own Facebook app to connect through.

    Creates the row when there is none: the app has to be saved *before* the
    OAuth flow can run through it, so this is routinely the first thing that
    ever writes an integration row for a company. It is stored
    ``disconnected`` because that is exactly what it is — configured, not yet
    authorised.

    Deliberately does not touch the token columns. Somebody correcting a typo
    in their app secret has not disconnected their pages, and wiping the
    tokens here would make a one-character fix cost a full reconnect.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_INTEGRATION_TABLE}
            (company_id, provider, status, app_id, app_secret_enc,
             webhook_verify_token, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, provider) DO UPDATE SET
            app_id = EXCLUDED.app_id,
            app_secret_enc = EXCLUDED.app_secret_enc,
            webhook_verify_token = EXCLUDED.webhook_verify_token,
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, provider, IntegrationStatus.DISCONNECTED, app_id,
            app_secret_enc, verify_token, now, now,
        ],
    )


def clear_company_app(
    company_id: int, provider: str = IntegrationProvider.META
) -> bool:
    """Go back to the deployment's own app.

    The stored tokens go with it. They were issued *by* the app being removed
    and are worthless to any other one — leaving them would show a workspace
    as connected while every Graph call failed.
    """
    now = timezone.now()
    return bool(execute(
        f"""
        UPDATE {B2B_INTEGRATION_TABLE}
           SET app_id = NULL, app_secret_enc = NULL, webhook_verify_token = NULL,
               access_token_enc = NULL, token_expires_at = NULL,
               status = %s, last_error = NULL, updated_at = %s
         WHERE company_id = %s AND provider = %s
        """,
        [IntegrationStatus.DISCONNECTED, now, company_id, provider],
    ))


def find_by_verify_token(
    token: str, provider: str = IntegrationProvider.META
) -> dict[str, Any] | None:
    """Which workspace's app is being configured.

    The subscription handshake carries a verify token and nothing else — no
    page, no company — so this is the only thing that can answer it for a
    company connecting through an app of their own.
    """
    if not token:
        return None
    return fetch_one(
        f"SELECT * FROM {B2B_INTEGRATION_TABLE} "
        f"WHERE provider = %s AND webhook_verify_token = %s",
        [provider, token],
    )


def set_integration_status(
    integration_id: int, status: str, *, error: str | None = None
) -> None:
    execute(
        f"UPDATE {B2B_INTEGRATION_TABLE} "
        f"SET status = %s, last_error = %s, updated_at = %s WHERE id = %s",
        [status, error, timezone.now(), integration_id],
    )


def mark_synced(integration_id: int) -> None:
    execute(
        f"UPDATE {B2B_INTEGRATION_TABLE} SET last_sync_at = %s, updated_at = %s "
        f"WHERE id = %s",
        [timezone.now(), timezone.now(), integration_id],
    )


def disconnect(company_id: int, provider: str = IntegrationProvider.META) -> bool:
    """Unplug it, and forget the token.

    The row stays so the screen can still say "ulanmagan" against a connection
    that existed, and so the leads already on the board keep a valid
    `integration_id`. The credential does not: an integration nobody is using
    should not leave a usable token in the database.
    """
    now = timezone.now()
    return bool(execute(
        f"""
        UPDATE {B2B_INTEGRATION_TABLE}
           SET status = %s, access_token_enc = NULL, token_expires_at = NULL,
               last_error = NULL, updated_at = %s
         WHERE company_id = %s AND provider = %s
        """,
        [IntegrationStatus.DISCONNECTED, now, company_id, provider],
    ))


def delete_pages(integration_id: int) -> int:
    return execute(
        f"DELETE FROM {B2B_INTEGRATION_PAGE_TABLE} WHERE integration_id = %s",
        [integration_id],
    )


# ─── Pages ────────────────────────────────────────────────────────────────────

def list_pages(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_INTEGRATION_PAGE_TABLE} "
        f"WHERE company_id = %s ORDER BY page_name, id",
        [company_id],
    )


def list_active_pages(company_id: int | None = None) -> list[dict[str, Any]]:
    sql = (
        f"SELECT * FROM {B2B_INTEGRATION_PAGE_TABLE} "
        f"WHERE is_active = TRUE AND access_token_enc IS NOT NULL"
    )
    params: list[Any] = []
    if company_id is not None:
        sql += " AND company_id = %s"
        params.append(company_id)
    return fetch_all(sql + " ORDER BY id", params)


def find_page(page_id: str) -> dict[str, Any] | None:
    """Which workspace owns this Facebook page.

    The one query that is not scoped by company, and deliberately: a webhook
    knows a page id and nothing else. The unique index on `page_id` is what
    makes the answer unambiguous.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_INTEGRATION_PAGE_TABLE} WHERE page_id = %s",
        [page_id],
    )


def find_page_by_row_id(page_row_id: int) -> dict[str, Any] | None:
    """A page by our own id, without a company to check it against.

    For the ingest worker, which was handed the id by the view that already
    resolved the company — asking it to carry a `company_id` it would only
    pass back would not make anything safer.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_INTEGRATION_PAGE_TABLE} WHERE id = %s", [page_row_id]
    )


def get_page(page_row_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_INTEGRATION_PAGE_TABLE} "
        f"WHERE id = %s AND company_id = %s",
        [page_row_id, company_id],
    )


def upsert_page(
    *,
    integration_id: int,
    company_id: int,
    page_id: str,
    page_name: str,
    access_token_enc: str,
    subscribed: bool = False,
    is_active: bool | None = None,
) -> dict[str, Any] | None:
    """Store a page, keeping whatever the workspace already decided about it.

    `is_active` is left alone on a reconnect unless the caller says otherwise:
    somebody who turned one of their four pages off and then reconnected the
    account meant to keep it off.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_INTEGRATION_PAGE_TABLE}
            (integration_id, company_id, page_id, page_name, access_token_enc,
             subscribed, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (page_id) DO UPDATE SET
            integration_id = EXCLUDED.integration_id,
            company_id = EXCLUDED.company_id,
            page_name = EXCLUDED.page_name,
            access_token_enc = EXCLUDED.access_token_enc,
            subscribed = EXCLUDED.subscribed,
            is_active = COALESCE(%s, {B2B_INTEGRATION_PAGE_TABLE}.is_active),
            last_error = NULL,
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            integration_id, company_id, page_id, page_name, access_token_enc,
            subscribed, True if is_active is None else is_active, now, now,
            is_active,
        ],
    )


def set_page_active(page_row_id: int, company_id: int, active: bool) -> dict[str, Any] | None:
    return fetch_one(
        f"UPDATE {B2B_INTEGRATION_PAGE_TABLE} SET is_active = %s, updated_at = %s "
        f"WHERE id = %s AND company_id = %s RETURNING *",
        [active, timezone.now(), page_row_id, company_id],
    )


def set_page_error(page_row_id: int, error: str | None) -> None:
    execute(
        f"UPDATE {B2B_INTEGRATION_PAGE_TABLE} SET last_error = %s, updated_at = %s "
        f"WHERE id = %s",
        [error, timezone.now(), page_row_id],
    )


def count_page_lead(page_row_id: int, integration_id: int) -> None:
    """One more lead through this page. Two counters, one statement each —
    the screen prints both and neither is worth a join to derive."""
    now = timezone.now()
    execute(
        f"UPDATE {B2B_INTEGRATION_PAGE_TABLE} "
        f"SET lead_count = lead_count + 1, last_lead_at = %s, updated_at = %s "
        f"WHERE id = %s",
        [now, now, page_row_id],
    )
    execute(
        f"UPDATE {B2B_INTEGRATION_TABLE} "
        f"SET lead_count = lead_count + 1, last_sync_at = %s, updated_at = %s "
        f"WHERE id = %s",
        [now, now, integration_id],
    )


# ─── Delivery log ─────────────────────────────────────────────────────────────

def claim_event(
    *,
    provider: str,
    external_id: str,
    company_id: int | None = None,
    page_id: str | None = None,
    payload: dict | None = None,
) -> dict[str, Any] | None:
    """Reserve one delivery, or say somebody else already has it.

    Returns the new row, or ``None`` when this `external_id` has been seen
    before. That is the whole idempotency guarantee: Meta retries a webhook it
    did not get a 200 for, and two workers can be holding the same retry at
    once, so "have we handled this?" has to be answered by the unique index
    rather than by a SELECT that another worker can run between.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_INTEGRATION_EVENT_TABLE}
            (provider, external_id, company_id, page_id, status, payload,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'received', %s::jsonb, %s, %s)
        ON CONFLICT (provider, external_id) DO NOTHING
        RETURNING *
        """,
        [
            provider, external_id, company_id, page_id,
            json.dumps(payload or {}), now, now,
        ],
    )


def finish_event(
    event_id: int, *, status: str, lead_id: int | None = None, error: str | None = None
) -> None:
    execute(
        f"UPDATE {B2B_INTEGRATION_EVENT_TABLE} "
        f"SET status = %s, lead_id = %s, error = %s, updated_at = %s WHERE id = %s",
        [status, lead_id, (error or "")[:1000] or None, timezone.now(), event_id],
    )


def release_event(event_id: int) -> None:
    """Give a failed delivery back, so Meta's retry can have another go.

    Only for failures that might not repeat — a timeout reaching Meta, our own
    database being briefly unavailable. A form we could not make sense of is
    kept as `failed`, because retrying it forever accomplishes nothing.
    """
    execute(f"DELETE FROM {B2B_INTEGRATION_EVENT_TABLE} WHERE id = %s", [event_id])


def recent_events(company_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_INTEGRATION_EVENT_TABLE} "
        f"WHERE company_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
        [company_id, limit],
    )


# ─── Who a Meta lead is filed under ───────────────────────────────────────────

def fallback_author(company_id: int, preferred_id: int | None = None) -> int | None:
    """Whose name a lead nobody typed goes under.

    `b2b_workspace_lead.author_id` is NOT NULL — every lead was raised by
    somebody — and a Meta lead was raised by a stranger on Facebook. The
    employee who connected the integration is the honest answer: it was their
    act that put the form on the board. If they have since left, any active
    owner will do, and the lead is unclaimed either way.
    """
    if preferred_id:
        row = fetch_one(
            f"SELECT id FROM {B2B_EMPLOYEE_TABLE} "
            f"WHERE id = %s AND company_id = %s AND is_active = TRUE",
            [preferred_id, company_id],
        )
        if row:
            return row["id"]
    row = fetch_one(
        f"SELECT id FROM {B2B_EMPLOYEE_TABLE} "
        f"WHERE company_id = %s AND is_active = TRUE "
        f"ORDER BY CASE WHEN role IN ('owner', 'lider') THEN 0 ELSE 1 END, id "
        f"LIMIT 1",
        [company_id],
    )
    return row["id"] if row else None
