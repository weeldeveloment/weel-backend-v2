"""Each employee's own AI key — the connection behind "AI yordamchi".

The assistant on the chat list used to run on one key for the whole
workspace, pasted by an owner on the integrations screen. That made the
assistant a manager's gift: a plain employee could only ask an owner to
connect something. Now everybody connects their own — Claude or ChatGPT,
with a key from the vendor's console, whatever their role — and the
workspace key (if an owner did connect one) is the fallback for anybody who
has not.

One row per employee, in ``b2b_employee_ai_key``. The key is stored the way
the workspace's is (`integrations.crypto`, Fernet) and never comes back out
in a payload: the app gets a hint (``sk-ant-…3fA2``) and the model list.
"""
from __future__ import annotations

import json
from typing import Any

from apps.shared.raw.db import execute, fetch_one

TABLE = "b2b_employee_ai_key"

STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"


def get(employee_id: int) -> dict[str, Any] | None:
    row = fetch_one(f"SELECT * FROM {TABLE} WHERE employee_id = %s", [employee_id])
    if row and isinstance(row.get("models"), str):
        try:
            row["models"] = json.loads(row["models"])
        except ValueError:
            row["models"] = []
    return row


def save(
    employee_id: int,
    *,
    provider: str,
    key_enc: str,
    key_hint: str,
    model: str | None,
    models: list[str],
) -> dict[str, Any] | None:
    """Connects (or reconnects) — a second paste replaces the first."""
    execute(
        f"""
        INSERT INTO {TABLE} (employee_id, provider, key_enc, key_hint, model, models, status, error)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, NULL)
        ON CONFLICT (employee_id) DO UPDATE SET
            provider = EXCLUDED.provider,
            key_enc = EXCLUDED.key_enc,
            key_hint = EXCLUDED.key_hint,
            model = EXCLUDED.model,
            models = EXCLUDED.models,
            status = EXCLUDED.status,
            error = NULL,
            updated_at = NOW()
        """,
        [employee_id, provider, key_enc, key_hint, model, json.dumps(models), STATUS_CONNECTED],
    )
    return get(employee_id)


def set_model(employee_id: int, model: str) -> dict[str, Any] | None:
    execute(
        f"UPDATE {TABLE} SET model = %s, updated_at = NOW() WHERE employee_id = %s",
        [model, employee_id],
    )
    return get(employee_id)


def set_status(employee_id: int, status: str, *, error: str | None = None) -> None:
    execute(
        f"UPDATE {TABLE} SET status = %s, error = %s, updated_at = NOW() WHERE employee_id = %s",
        [status, error, employee_id],
    )


def delete(employee_id: int) -> None:
    execute(f"DELETE FROM {TABLE} WHERE employee_id = %s", [employee_id])
