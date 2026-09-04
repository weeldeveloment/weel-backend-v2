"""Raw-SQL data access for the AI assistants' history.

Same conventions as `repository.py`: dicts in and out, every query scoped by
`company_id` and `provider`. The connection row itself is the shared
`b2b_integration` table — `repository.get_integration` and friends — and this
file owns the three tables that hang off it: projects, chats, turns.
"""
from __future__ import annotations

import json
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from apps.shared.raw.db import _apply_schema_context, execute, fetch_all, fetch_one

from apps.b2b.integrations.ai_import import ExportBundle
from apps.b2b.raw.tables import (
    B2B_AI_CONVERSATION_TABLE,
    B2B_AI_MESSAGE_TABLE,
    B2B_AI_PROJECT_TABLE,
    B2B_INTEGRATION_TABLE,
)

SOURCE_IMPORT = "import"
SOURCE_APP = "app"


# ─── The connection's own columns ────────────────────────────────────────────

def ensure_row(company_id: int, provider: str) -> dict[str, Any] | None:
    """The connection row, made if there is none.

    An import may come before a key — the history is worth reading on its
    own — and the counters and the import stamp need a row to sit on. Made
    ``disconnected``, which is what a row with no key is.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_INTEGRATION_TABLE}
            (company_id, provider, status, created_at, updated_at)
        VALUES (%s, %s, 'disconnected', %s, %s)
        ON CONFLICT (company_id, provider) DO UPDATE SET updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [company_id, provider, now, now],
    )


def set_models(integration_id: int, model: str | None, models: list[str]) -> None:
    execute(
        f"UPDATE {B2B_INTEGRATION_TABLE} "
        f"SET ai_model = %s, ai_models = %s::jsonb, updated_at = %s WHERE id = %s",
        [model, json.dumps(models), timezone.now(), integration_id],
    )


def set_model(company_id: int, provider: str, model: str) -> dict[str, Any] | None:
    return fetch_one(
        f"UPDATE {B2B_INTEGRATION_TABLE} SET ai_model = %s, updated_at = %s "
        f"WHERE company_id = %s AND provider = %s RETURNING *",
        [model, timezone.now(), company_id, provider],
    )


def mark_imported(integration_id: int) -> None:
    now = timezone.now()
    execute(
        f"UPDATE {B2B_INTEGRATION_TABLE} "
        f"SET last_import_at = %s, last_sync_at = %s, updated_at = %s WHERE id = %s",
        [now, now, now, integration_id],
    )


def models_of(integration: dict[str, Any] | None) -> list[str]:
    raw = (integration or {}).get("ai_models")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return [m for m in raw if isinstance(m, str)] if isinstance(raw, list) else []


# ─── Counts for the screen ───────────────────────────────────────────────────

def counts(company_id: int, provider: str) -> dict[str, int]:
    row = fetch_one(
        f"""
        SELECT
            (SELECT COUNT(*) FROM {B2B_AI_CONVERSATION_TABLE}
              WHERE company_id = %s AND provider = %s) AS chats,
            (SELECT COUNT(*) FROM {B2B_AI_PROJECT_TABLE}
              WHERE company_id = %s AND provider = %s) AS projects,
            (SELECT COALESCE(SUM(message_count), 0) FROM {B2B_AI_CONVERSATION_TABLE}
              WHERE company_id = %s AND provider = %s) AS messages
        """,
        [company_id, provider] * 3,
    ) or {}
    return {
        "chat_count": int(row.get("chats") or 0),
        "project_count": int(row.get("projects") or 0),
        "message_count": int(row.get("messages") or 0),
    }


# ─── Projects ────────────────────────────────────────────────────────────────

def list_projects(company_id: int, provider: str) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT p.*,
               (SELECT COUNT(*) FROM {B2B_AI_CONVERSATION_TABLE} c
                 WHERE c.project_id = p.id) AS chat_count
          FROM {B2B_AI_PROJECT_TABLE} p
         WHERE p.company_id = %s AND p.provider = %s
         ORDER BY COALESCE(p.external_created_at, p.created_at) DESC, p.id DESC
        """,
        [company_id, provider],
    )


def get_project(project_id: int, company_id: int, provider: str) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_AI_PROJECT_TABLE} "
        f"WHERE id = %s AND company_id = %s AND provider = %s",
        [project_id, company_id, provider],
    )


def upsert_project(
    *,
    company_id: int,
    provider: str,
    external_id: str,
    name: str,
    description: str | None,
    instructions: str | None,
    external_created_at,
    created_by_id: int | None,
) -> dict[str, Any] | None:
    """One project per vendor id. Importing the same export again updates
    the name and instructions rather than making a second folder."""
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_AI_PROJECT_TABLE}
            (company_id, provider, external_id, name, description, instructions,
             created_by_id, external_created_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, provider, external_id) WHERE external_id IS NOT NULL
        DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            instructions = EXCLUDED.instructions,
            external_created_at = COALESCE(EXCLUDED.external_created_at,
                                           {B2B_AI_PROJECT_TABLE}.external_created_at),
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, provider, external_id, name[:300], description,
            instructions, created_by_id, external_created_at, now, now,
        ],
    )


# ─── Conversations ───────────────────────────────────────────────────────────

def list_conversations(
    company_id: int,
    provider: str,
    *,
    project_id: int | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Newest first, by the last turn in them.

    `project_id` narrows to one project; `query` is a case-insensitive match
    on the title — enough to find "the one about the invoice" in a few
    hundred chats without a search index.
    """
    where = ["c.company_id = %s", "c.provider = %s"]
    params: list[Any] = [company_id, provider]
    if project_id is not None:
        where.append("c.project_id = %s")
        params.append(project_id)
    if query:
        where.append("c.title ILIKE %s")
        params.append(f"%{query.strip()}%")
    params.extend([limit, offset])
    return fetch_all(
        f"""
        SELECT c.*, p.name AS project_name
          FROM {B2B_AI_CONVERSATION_TABLE} c
          LEFT JOIN {B2B_AI_PROJECT_TABLE} p ON p.id = c.project_id
         WHERE {' AND '.join(where)}
         ORDER BY COALESCE(c.last_message_at, c.external_updated_at, c.created_at) DESC,
                  c.id DESC
         LIMIT %s OFFSET %s
        """,
        params,
    )


def count_conversations(
    company_id: int,
    provider: str,
    *,
    project_id: int | None = None,
    query: str | None = None,
) -> int:
    where = ["company_id = %s", "provider = %s"]
    params: list[Any] = [company_id, provider]
    if project_id is not None:
        where.append("project_id = %s")
        params.append(project_id)
    if query:
        where.append("title ILIKE %s")
        params.append(f"%{query.strip()}%")
    row = fetch_one(
        f"SELECT COUNT(*) AS n FROM {B2B_AI_CONVERSATION_TABLE} "
        f"WHERE {' AND '.join(where)}",
        params,
    )
    return int((row or {}).get("n") or 0)


def get_conversation(
    conversation_id: int, company_id: int, provider: str
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT c.*, p.name AS project_name, p.instructions AS project_instructions
          FROM {B2B_AI_CONVERSATION_TABLE} c
          LEFT JOIN {B2B_AI_PROJECT_TABLE} p ON p.id = c.project_id
         WHERE c.id = %s AND c.company_id = %s AND c.provider = %s
        """,
        [conversation_id, company_id, provider],
    )


def find_owned_conversation(
    company_id: int, provider: str, employee_id: int
) -> dict[str, Any] | None:
    """The one app-started chat a person owns under a provider — how the
    workspace assistant finds its per-employee room (see `assistant.py`)."""
    return fetch_one(
        f"""
        SELECT c.*, NULL AS project_name, NULL AS project_instructions
          FROM {B2B_AI_CONVERSATION_TABLE} c
         WHERE c.company_id = %s AND c.provider = %s
           AND c.created_by_id = %s AND c.source = %s
         ORDER BY c.id ASC
         LIMIT 1
        """,
        [company_id, provider, employee_id, SOURCE_APP],
    )


def create_conversation(
    *,
    company_id: int,
    provider: str,
    title: str,
    model: str | None,
    project_id: int | None,
    created_by_id: int | None,
) -> dict[str, Any] | None:
    """A chat started in the app. No vendor id: it was never at the vendor."""
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_AI_CONVERSATION_TABLE}
            (company_id, provider, external_id, project_id, title, model, source,
             created_by_id, message_count, last_message_at, created_at, updated_at)
        VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, 0, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, provider, project_id, title[:300], model, SOURCE_APP,
            created_by_id, now, now, now,
        ],
    )


def set_title(conversation_id: int, title: str) -> None:
    execute(
        f"UPDATE {B2B_AI_CONVERSATION_TABLE} SET title = %s, updated_at = %s WHERE id = %s",
        [title[:300], timezone.now(), conversation_id],
    )


def delete_conversation(conversation_id: int, company_id: int, provider: str) -> bool:
    return bool(execute(
        f"DELETE FROM {B2B_AI_CONVERSATION_TABLE} "
        f"WHERE id = %s AND company_id = %s AND provider = %s",
        [conversation_id, company_id, provider],
    ))


# ─── Messages ────────────────────────────────────────────────────────────────

def list_messages(conversation_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_AI_MESSAGE_TABLE} WHERE conversation_id = %s "
        f"ORDER BY position, id",
        [conversation_id],
    )


def recent_messages(conversation_id: int, limit: int) -> list[dict[str, Any]]:
    """The last `limit` turns, in order. What gets sent to the vendor."""
    rows = fetch_all(
        f"SELECT * FROM {B2B_AI_MESSAGE_TABLE} WHERE conversation_id = %s "
        f"ORDER BY position DESC, id DESC LIMIT %s",
        [conversation_id, limit],
    )
    rows.reverse()
    return rows


def append_message(conversation_id: int, role: str, text: str) -> dict[str, Any] | None:
    """Adds a turn at the end and moves the chat's counters with it."""
    now = timezone.now()
    row = fetch_one(
        f"""
        INSERT INTO {B2B_AI_MESSAGE_TABLE}
            (conversation_id, external_id, role, text, position, sent_at, created_at)
        VALUES (
            %s, NULL, %s, %s,
            COALESCE((SELECT MAX(position) + 1 FROM {B2B_AI_MESSAGE_TABLE}
                       WHERE conversation_id = %s), 0),
            %s, %s
        )
        RETURNING *
        """,
        [conversation_id, role, text, conversation_id, now, now],
    )
    execute(
        f"""
        UPDATE {B2B_AI_CONVERSATION_TABLE}
           SET message_count = (SELECT COUNT(*) FROM {B2B_AI_MESSAGE_TABLE}
                                 WHERE conversation_id = %s),
               last_message_at = %s, updated_at = %s
         WHERE id = %s
        """,
        [conversation_id, now, now, conversation_id],
    )
    return row


# ─── Import ──────────────────────────────────────────────────────────────────

def store_bundle(
    *,
    company_id: int,
    provider: str,
    bundle: ExportBundle,
    employee_id: int | None,
) -> dict[str, int]:
    """Everything an export held, into the tables. Answers what it did.

    One transaction: an export half-imported when the connection dropped
    would show chats with no turns, and the retry would have to know which.
    A chat already here (same vendor id) has its turns replaced with the
    export's — the export is the newer copy, and a chat continued in the app
    since is a chat the person also continued at the vendor's.
    """
    created_chats = updated_chats = 0
    with transaction.atomic():
        project_ids: dict[str, int] = {}
        for project in bundle.projects:
            row = upsert_project(
                company_id=company_id,
                provider=provider,
                external_id=project.external_id,
                name=project.name,
                description=project.description,
                instructions=project.instructions,
                external_created_at=project.created_at,
                created_by_id=employee_id,
            )
            if row:
                project_ids[project.external_id] = row["id"]
        # A chat may name a project the export did not describe (ChatGPT's
        # do; a Claude export may too if projects.json was left out). Look
        # those up so the chat still lands in the right folder next time.
        for external_id in {
            c.project_external_id for c in bundle.conversations if c.project_external_id
        } - set(project_ids):
            row = fetch_one(
                f"SELECT id FROM {B2B_AI_PROJECT_TABLE} "
                f"WHERE company_id = %s AND provider = %s AND external_id = %s",
                [company_id, provider, external_id],
            )
            if row:
                project_ids[external_id] = row["id"]

        now = timezone.now()
        for conversation in bundle.conversations:
            last_at = None
            for message in reversed(conversation.messages):
                if message.sent_at:
                    last_at = message.sent_at
                    break
            last_at = last_at or conversation.updated_at or conversation.created_at
            row = fetch_one(
                f"""
                INSERT INTO {B2B_AI_CONVERSATION_TABLE}
                    (company_id, provider, external_id, project_id, title, model,
                     source, created_by_id, message_count, last_message_at,
                     external_created_at, external_updated_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, provider, external_id) WHERE external_id IS NOT NULL
                DO UPDATE SET
                    project_id = COALESCE(EXCLUDED.project_id,
                                          {B2B_AI_CONVERSATION_TABLE}.project_id),
                    title = EXCLUDED.title,
                    model = COALESCE(EXCLUDED.model, {B2B_AI_CONVERSATION_TABLE}.model),
                    message_count = EXCLUDED.message_count,
                    last_message_at = EXCLUDED.last_message_at,
                    external_updated_at = EXCLUDED.external_updated_at,
                    updated_at = EXCLUDED.updated_at
                RETURNING id, (xmax = 0) AS inserted
                """,
                [
                    company_id, provider, conversation.external_id,
                    project_ids.get(conversation.project_external_id or ""),
                    conversation.title, conversation.model, SOURCE_IMPORT,
                    employee_id, len(conversation.messages), last_at,
                    conversation.created_at, conversation.updated_at, now, now,
                ],
            )
            if not row:
                continue
            if row.get("inserted"):
                created_chats += 1
            else:
                updated_chats += 1
            _replace_messages(row["id"], conversation.messages)

    return {
        "projects": len(bundle.projects),
        "chats_created": created_chats,
        "chats_updated": updated_chats,
        "messages": bundle.message_count,
    }


def _replace_messages(conversation_id: int, messages) -> None:
    execute(
        f"DELETE FROM {B2B_AI_MESSAGE_TABLE} WHERE conversation_id = %s",
        [conversation_id],
    )
    if not messages:
        return
    now = timezone.now()
    rows = [
        (
            conversation_id, message.external_id, message.role, message.text,
            position, message.sent_at, now,
        )
        for position, message in enumerate(messages)
    ]
    # `executemany` rather than one round trip per turn: a heavy user's export
    # is tens of thousands of turns, and the import has to finish inside one
    # request.
    with connection.cursor() as cursor:
        _apply_schema_context(cursor)
        cursor.executemany(
            f"INSERT INTO {B2B_AI_MESSAGE_TABLE} "
            f"(conversation_id, external_id, role, text, position, sent_at, created_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
