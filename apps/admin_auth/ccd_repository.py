"""Read model for the Call Center Desk (`weelccd`).

CCD is WEEL's own support desk, so unlike everything under `apps/b2b/` — which is
always scoped to the caller's own workspace — these queries deliberately look across
every company. They are read-only except for the few desk actions at the bottom.

The product/schema naming split applies throughout (see the note in
`create_b2b_tables.py`):

    CCD "company"    →  a `b2b_org` row
    CCD "workspace"  →  a `b2b_company` row

Each list is answered with one aggregate query rather than a query per row: the desk
opens on a table of every company, and an N+1 there is the difference between a page
that loads and one that does not.
"""

from __future__ import annotations

from typing import Any

from shared.raw.db import fetch_all, fetch_one

# ─── Companies (b2b_org) ──────────────────────────────────────────────────────

# Per-org rollups. Each CTE is grouped by org so the outer query stays a plain join.
_COMPANY_BASE = """
    WITH ws AS (
        SELECT org_id,
               COUNT(*)                                  AS workspaces,
               MIN(id)                                   AS primary_workspace_id,
               MAX(updated_at)                           AS ws_touched_at
        FROM b2b_company
        WHERE org_id IS NOT NULL
        GROUP BY org_id
    ),
    emp AS (
        -- Counts exactly the population `list_employees` returns, blocked people
        -- included: an agent who reads "12 users" and opens the roster has to find
        -- twelve rows there, and a blocked account is still one the desk deals with.
        SELECT c.org_id, COUNT(e.id) AS users
        FROM b2b_company c
        JOIN b2b_employee e ON e.company_id = c.id
        WHERE c.org_id IS NOT NULL
          AND NOT COALESCE(e.is_guest, FALSE)
          AND NOT COALESCE(e.is_hidden, FALSE)
        GROUP BY c.org_id
    ),
    trips AS (
        SELECT c.org_id,
               COUNT(t.id)         AS trips,
               MAX(t.updated_at)   AS trip_touched_at
        FROM b2b_company c
        JOIN b2b_business_trip t ON t.company_id = c.id
        WHERE c.org_id IS NOT NULL
        GROUP BY c.org_id
    ),
    tickets AS (
        -- "Open" means the employee has written and no staff line has landed since.
        SELECT c.org_id,
               COUNT(*)            AS open_tickets,
               MAX(s.created_at)   AS ticket_touched_at
        FROM b2b_company c
        JOIN b2b_support_message s ON s.company_id = c.id
        WHERE c.org_id IS NOT NULL AND NOT s.is_staff AND s.read_at IS NULL
        GROUP BY c.org_id
    )
    SELECT o.id,
           o.name,
           o.is_active,
           o.created_at                                    AS registered_at,
           COALESCE(o.tax_id, pw.inn)                      AS inn,
           COALESCE(pw.legal_name, o.name)                 AS legal,
           pw.city                                         AS city,
           TRIM(COALESCE(ou.first_name, '') || ' ' || COALESCE(ou.last_name, '')) AS director,
           ou.phone                                        AS phone,
           ou.email                                        AS email,
           COALESCE(ws.workspaces, 0)                      AS workspaces,
           COALESCE(emp.users, 0)                          AS users,
           COALESCE(trips.trips, 0)                        AS trips,
           COALESCE(tickets.open_tickets, 0)               AS open_tickets,
           GREATEST(
               o.updated_at,
               COALESCE(ws.ws_touched_at,      o.updated_at),
               COALESCE(trips.trip_touched_at, o.updated_at),
               COALESCE(tickets.ticket_touched_at, o.updated_at)
           )                                               AS last_activity_at
    FROM b2b_org o
    LEFT JOIN ws      ON ws.org_id      = o.id
    LEFT JOIN emp     ON emp.org_id     = o.id
    LEFT JOIN trips   ON trips.org_id   = o.id
    LEFT JOIN tickets ON tickets.org_id = o.id
    LEFT JOIN b2b_company pw ON pw.id   = ws.primary_workspace_id
    LEFT JOIN b2b_user    ou ON ou.id   = o.owner_user_id
"""


def list_companies(*, search: str | None = None) -> list[dict[str, Any]]:
    sql = _COMPANY_BASE
    params: list[Any] = []
    if search:
        sql += """
        WHERE o.name ILIKE %s OR COALESCE(o.tax_id, pw.inn) ILIKE %s OR pw.legal_name ILIKE %s
        """
        like = f"%{search}%"
        params = [like, like, like]
    sql += " ORDER BY o.name ASC"
    return fetch_all(sql, params)


def get_company(org_id: int) -> dict[str, Any] | None:
    return fetch_one(_COMPANY_BASE + " WHERE o.id = %s", [org_id])


# ─── Workspaces (b2b_company) ─────────────────────────────────────────────────

def list_workspaces(*, org_id: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT c.id,
               c.org_id                                     AS company_id,
               c.name,
               c.is_active,
               c.created_at,
               TRIM(COALESCE(ou.first_name, '') || ' ' || COALESCE(ou.last_name, '')) AS owner,
               COALESCE(m.members, 0)                       AS members,
               COALESCE(t.trips, 0)                         AS trips,
               dr.status                                    AS delete_request_status
        FROM b2b_company c
        LEFT JOIN b2b_org o  ON o.id  = c.org_id
        LEFT JOIN b2b_user ou ON ou.id = o.owner_user_id
        LEFT JOIN (
            SELECT company_id, COUNT(*) AS members
            FROM b2b_employee WHERE is_active GROUP BY company_id
        ) m ON m.company_id = c.id
        LEFT JOIN (
            SELECT company_id, COUNT(*) AS trips
            FROM b2b_business_trip GROUP BY company_id
        ) t ON t.company_id = c.id
        LEFT JOIN LATERAL (
            SELECT status FROM b2b_workspace_delete_request
            WHERE company_id = c.id ORDER BY created_at DESC LIMIT 1
        ) dr ON TRUE
    """
    params: list[Any] = []
    if org_id is not None:
        sql += " WHERE c.org_id = %s"
        params.append(org_id)
    sql += " ORDER BY c.name ASC"
    return fetch_all(sql, params)


# ─── People (b2b_employee) ────────────────────────────────────────────────────

def list_employees(*, search: str | None = None, org_id: int | None = None) -> list[dict[str, Any]]:
    """Every company's roster at once — the desk's Users screen.

    `is_guest` rows are people lent in from another workspace and `is_hidden` rows are
    deliberately kept off rosters, so neither belongs on a support agent's list.
    """
    sql = """
        SELECT e.id,
               e.full_name                AS name,
               e.phone,
               e.email,
               e.position,
               e.role,
               e.status,
               e.is_active,
               e.updated_at,
               e.company_id               AS workspace_id,
               c.name                     AS workspace_name,
               c.org_id                   AS company_id,
               o.name                     AS company_name,
               d.name                     AS department,
               (e.fcm_token IS NOT NULL)  AS has_push_token
        FROM b2b_employee e
        LEFT JOIN b2b_company c    ON c.id = e.company_id
        LEFT JOIN b2b_org o        ON o.id = c.org_id
        LEFT JOIN b2b_department d ON d.id = e.department_id
        WHERE NOT COALESCE(e.is_guest, FALSE) AND NOT COALESCE(e.is_hidden, FALSE)
    """
    params: list[Any] = []
    if org_id is not None:
        sql += " AND c.org_id = %s"
        params.append(org_id)
    if search:
        sql += " AND (e.full_name ILIKE %s OR e.phone ILIKE %s OR e.email ILIKE %s)"
        like = f"%{search}%"
        params += [like, like, like]
    sql += " ORDER BY e.full_name ASC"
    return fetch_all(sql, params)


# ─── Calls (b2b_call) ─────────────────────────────────────────────────────────

def list_calls(*, limit: int = 200) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT k.id,
               k.company_id            AS workspace_id,
               c.org_id                AS company_id,
               k.type,
               k.status,
               k.source_module,
               k.duration_seconds,
               k.started_at,
               k.answered_at,
               k.ended_at,
               COALESCE(ie.full_name, '')  AS initiator_name,
               COALESCE(te.full_name, '')  AS target_name,
               COALESCE(te.phone, ie.phone, '') AS phone
        FROM b2b_call k
        LEFT JOIN b2b_company c  ON c.id = k.company_id
        LEFT JOIN b2b_employee ie ON ie.id = k.initiator_id
        LEFT JOIN b2b_employee te ON te.id = k.target_employee_id
        ORDER BY k.started_at DESC NULLS LAST, k.id DESC
        LIMIT %s
        """,
        [limit],
    )


# ─── Audit (b2b_audit_event) ──────────────────────────────────────────────────

def list_audit(*, limit: int = 300) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT a.id,
               a.action,
               a.target_type,
               a.target_id,
               a.payload,
               a.created_at,
               a.company_id             AS workspace_id,
               c.org_id                 AS company_id,
               c.name                   AS workspace_name,
               COALESCE(e.full_name, '') AS actor_name
        FROM b2b_audit_event a
        LEFT JOIN b2b_company c  ON c.id = a.company_id
        LEFT JOIN b2b_employee e ON e.id = a.actor_employee_id
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT %s
        """,
        [limit],
    )


# ─── Approvals ────────────────────────────────────────────────────────────────

def list_approvals() -> list[dict[str, Any]]:
    """The three request queues the desk arbitrates, as one list.

    They live in separate tables because they carry different payloads; the desk shows
    them in a single Approval Center, so the union happens here rather than in three
    round trips. `key` is what a decision is addressed to.
    """
    return fetch_all(
        """
        SELECT 'workspace_delete:' || r.id::text AS key,
               'workspace_delete'                AS kind,
               r.id                              AS request_id,
               r.company_id                      AS workspace_id,
               c.org_id                          AS company_id,
               c.name                            AS workspace_name,
               r.reason,
               r.status,
               r.created_at,
               COALESCE(e.full_name, '')         AS requested_by_name
        FROM b2b_workspace_delete_request r
        LEFT JOIN b2b_company c  ON c.id = r.company_id
        LEFT JOIN b2b_employee e ON e.id = r.requested_by

        UNION ALL

        SELECT 'ownership:' || r.id::text,
               'ownership_' || COALESCE(r.kind, 'transfer'),
               r.id,
               r.company_id,
               c.org_id,
               c.name,
               r.reason,
               r.status,
               r.created_at,
               COALESCE(e.full_name, '')
        FROM b2b_ownership_request r
        LEFT JOIN b2b_company c  ON c.id = r.company_id
        LEFT JOIN b2b_employee e ON e.id = r.requested_by

        UNION ALL

        SELECT 'join:' || r.id::text,
               'join',
               r.id,
               r.company_id,
               c.org_id,
               c.name,
               r.message,
               r.status,
               r.created_at,
               COALESCE(a.first_name || ' ' || a.last_name, a.username, '')
        FROM b2b_join_request r
        LEFT JOIN b2b_company c ON c.id = r.company_id
        LEFT JOIN b2b_account a ON a.id = r.account_id

        ORDER BY created_at DESC
        """
    )


# ─── Support threads ──────────────────────────────────────────────────────────

def list_support_threads(*, search: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """The support inbox, one row per person who has written in.

    `apps/b2b/workspace/repository.list_support_threads` answers the same question, but
    its `company_id` is the workspace's — which is what the mobile app needs. The desk
    links a ticket to a *company*, so this variant carries the org id as well rather than
    making the caller guess which of the two a bare `company_id` means.

    A thread is derived, not stored: there is no thread table, so an employee with
    messages is a thread.
    """
    where = ""
    params: list[Any] = []
    if search:
        where = "WHERE e.full_name ILIKE %s OR c.name ILIKE %s OR e.phone ILIKE %s"
        needle = f"%{search}%"
        params = [needle, needle, needle]
    params.append(limit)
    return fetch_all(
        f"""
        SELECT e.id                        AS employee_id,
               e.full_name,
               e.phone,
               c.id                        AS workspace_id,
               c.name                      AS workspace_name,
               c.org_id                    AS company_id,
               o.name                      AS company_name,
               COUNT(m.id)                 AS message_count,
               COUNT(m.id) FILTER (WHERE m.is_staff = FALSE AND m.read_at IS NULL) AS unread_count,
               MIN(m.created_at) FILTER (WHERE m.is_staff = FALSE AND m.read_at IS NULL) AS waiting_since,
               MAX(m.created_at)           AS last_message_at,
               (
                   SELECT text FROM b2b_support_message latest
                   WHERE latest.employee_id = e.id
                   ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
               )                           AS last_message
        FROM b2b_support_message m
        JOIN b2b_employee e ON e.id = m.employee_id
        LEFT JOIN b2b_company c ON c.id = m.company_id
        LEFT JOIN b2b_org o     ON o.id = c.org_id
        {where}
        GROUP BY e.id, e.full_name, e.phone, c.id, c.name, c.org_id, o.name
        ORDER BY MAX(m.created_at) DESC
        LIMIT %s
        """,
        params,
    )


def support_messages(employee_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT m.id, m.text, m.is_staff, m.created_at, m.read_at,
               COALESCE(e.full_name, '') AS employee_name
        FROM b2b_support_message m
        LEFT JOIN b2b_employee e ON e.id = m.employee_id
        WHERE m.employee_id = %s
        ORDER BY m.created_at ASC, m.id ASC
        """,
        [employee_id],
    )


# ─── Desk actions ─────────────────────────────────────────────────────────────

def set_employee_active(employee_id: int, *, active: bool) -> dict[str, Any] | None:
    return fetch_one(
        "UPDATE b2b_employee SET is_active = %s, updated_at = NOW() WHERE id = %s "
        "RETURNING id, full_name, is_active",
        [active, employee_id],
    )


def set_workspace_active(workspace_id: int, *, active: bool) -> dict[str, Any] | None:
    """The desk's freeze/unfreeze. The schema has no separate frozen flag: an inactive
    workspace is already excluded everywhere a company's data is read."""
    return fetch_one(
        "UPDATE b2b_company SET is_active = %s, updated_at = NOW() WHERE id = %s "
        "RETURNING id, name, is_active",
        [active, workspace_id],
    )
