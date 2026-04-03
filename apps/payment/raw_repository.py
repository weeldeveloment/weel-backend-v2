from __future__ import annotations

from typing import Any

from django.utils import timezone

from shared.raw.compat import get_table_name, return_star
from shared.raw.db import execute, fetch_one


def get_latest_transaction_history_for_booking(booking_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT *
        FROM {get_table_name("transaction_history")}
        WHERE booking_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        [booking_id],
    )


def mark_latest_transaction_dismissed(booking_id: int) -> int:
    return execute(
        f"""
        UPDATE {get_table_name("transaction_history")} th
        SET status = 'DISMISSED',
            updated_at = %s
        WHERE th.id = (
            SELECT id
            FROM {get_table_name("transaction_history")}
            WHERE booking_id = %s
            ORDER BY id DESC
            LIMIT 1
        )
        """,
        [timezone.now(), booking_id],
    )


def create_hold_transaction(
    *,
    booking_id: int,
    client_user_id: int,
    partner_user_id: int,
    amount,
    transaction_id: str | None,
    hold_id: str | None,
    card_id: str | None = None,
    extra_id: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {get_table_name("transaction_history")} (
            booking_id,
            client_user_id,
            partner_user_id,
            amount,
            currency,
            transaction_id,
            hold_id,
            type,
            status,
            card_id,
            extra_id,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            %s,
            %s,
            'UZS',
            %s,
            %s,
            'HOLD',
            'PENDING',
            %s,
            %s,
            %s,
            %s
        )
        {"RETURNING *" if return_star() else ""}
        """,
        [
            booking_id,
            client_user_id,
            partner_user_id,
            amount,
            transaction_id,
            hold_id,
            card_id,
            extra_id,
            now,
            now,
        ],
    )


def create_charge_transaction_from_latest(
    *,
    booking_row: dict[str, Any],
    transaction_id: str | None,
    hold_id: str | None,
    amount,
    card_id: str | None = None,
    extra_id: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {get_table_name("transaction_history")} (
            booking_id,
            client_user_id,
            partner_user_id,
            amount,
            currency,
            transaction_id,
            hold_id,
            type,
            status,
            card_id,
            extra_id,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            %s,
            %s,
            'UZS',
            %s,
            %s,
            'CHRG',
            'CHARGED',
            %s,
            %s,
            %s,
            %s
        )
        {"RETURNING *" if return_star() else ""}
        """,
        [
            int(booking_row["id"]),
            int(booking_row["client_user_id"]),
            int(booking_row["partner_user_id"]) if booking_row.get("partner_user_id") else None,
            amount,
            transaction_id,
            hold_id,
            card_id,
            extra_id,
            now,
            now,
        ],
    )
