"""Putting back together what was opened twice.

Three shapes of the same complaint — "this workspace is in my list twice":

* **one person, two seats in one workspace.** Every door in (a link, a join
  request, the owners seated on a new room, the dashboard's employee form, a
  secondment) wrote its own roster row without asking whether the person had
  one, and the start-up link of roster rows to accounts by phone
  (`create_b2b_tables`) then joined the rows up as one person. Each row is a
  line in the switcher.
* **one company, two workspaces of the same name**, one of them never used.
* **one owner, two companies of the same name**, the second of them never
  used — "Kompaniya yaratish" answered slowly and tapped again.

The doors are closed in `accounts` (`create_membership`, `create_workspace`)
and in the secondment views. This is what cleans up after them, run by
`create_b2b_tables` on every start-up and by `manage.py dedupe_workspaces` by
hand. Idempotent: on a clean database it finds nothing and writes nothing.

What it will and will not do is deliberately lopsided:

* Two seats of one person **are merged**: every row that points at the extra
  seat — tasks, messages, chat memberships, attendance, everything that
  references `b2b_employee(id)` — is moved onto the seat that stays, and the
  extra one is retired. Nothing anybody wrote is lost; it is the same person.
* A same-named workspace or company is **retired only if it is an empty
  copy** — nothing in it but the seats and bookkeeping its creation wrote,
  and nobody on it who is not also in the one that stays. Two copies that
  both hold work, or hold different people, are not a double tap; which one
  goes is a person's call, so those are reported in the log and left.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

from django.db import IntegrityError, DatabaseError, transaction

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.workspace.accounts import name_key

logger = logging.getLogger(__name__)

Log = Callable[[str], Any]

#: The order a seat is kept in, best first: a full member over a guest over
#: somebody only in one chat, then the higher role, then the older row.
_KEEP_ORDER = """
    e.is_chat_only ASC,
    e.is_guest ASC,
    CASE e.role WHEN 'owner' THEN 5 WHEN 'admin' THEN 4 WHEN 'manager' THEN 3
                WHEN 'employee' THEN 2 ELSE 1 END DESC,
    e.id ASC
"""

#: Tables a workspace fills in by merely existing or being opened — its
#: roster, its audit trail, the rooms and settings made on first open, the
#: invitations and requests about who may come in. Rows here do not make a
#: workspace "used". Anything *not* listed counts as work, which errs on the
#: side of leaving a workspace alone.
_BOOKKEEPING = frozenset({
    "b2b_employee",
    "b2b_audit_event",
    "b2b_chat_thread",
    "b2b_notification",
    "b2b_workspace_role",
    "b2b_inventory_settings",
    "b2b_warehouse",
    "b2b_department",
    "b2b_travel_policy",
    "b2b_attendance_location",
    "b2b_report_subscription",
    "b2b_workspace_invite",
    "b2b_join_request",
    "b2b_workspace_request",
    "b2b_workspace_membership",
    "b2b_workspace_delete_request",
    "b2b_ownership_request",
    "b2b_user",
})

#: `(table, column)` pairs that point at a roster row but are not moved when
#: two rows are merged. A secondment's row names the guest seat it created;
#: that secondment is ended, not handed to the seat that stays.
_NOT_MOVED = frozenset({("b2b_workspace_membership", "employee_id")})


def run(*, dry_run: bool = False, log: Log = logger.info) -> dict[str, int]:
    """All three passes, seats first: merging seats is what can leave a
    same-named workspace with nobody but the people also in its twin."""
    report = {
        "seats_merged": merge_duplicate_seats(dry_run=dry_run, log=log),
        "workspaces_retired": 0,
        "companies_retired": 0,
        "left_for_a_person": 0,
    }
    retired, left = retire_duplicate_workspaces(dry_run=dry_run, log=log)
    report["workspaces_retired"] = retired
    report["left_for_a_person"] += left
    retired, left = retire_duplicate_companies(dry_run=dry_run, log=log)
    report["companies_retired"] = retired
    report["left_for_a_person"] += left
    return report


# ─── One person, two seats ────────────────────────────────────────────────────

def duplicate_seat_groups() -> list[dict[str, Any]]:
    """Every workspace where one person holds more than one live seat, with
    the seats in the order they would be kept in.

    The person is the account — a guest row made before the start-up link
    reached it is recognised through the account of the row it was lent
    from."""
    return fetch_all(
        f"""
        SELECT e.company_id,
               COALESCE(e.account_id, h.account_id) AS account_id,
               array_agg(e.id ORDER BY {_KEEP_ORDER}) AS ids
          FROM b2b_employee e
          LEFT JOIN b2b_employee h ON h.id = e.home_employee_id
         WHERE e.is_active = TRUE
           AND COALESCE(e.account_id, h.account_id) IS NOT NULL
         GROUP BY e.company_id, COALESCE(e.account_id, h.account_id)
        HAVING COUNT(*) > 1
         ORDER BY e.company_id, 2
        """
    )


def merge_duplicate_seats(*, dry_run: bool = False, log: Log = logger.info) -> int:
    groups = duplicate_seat_groups()
    if not groups:
        return 0
    references = _references_to("b2b_employee")
    merged = 0
    for group in groups:
        keeper, *extras = list(group["ids"])
        for extra in extras:
            log(
                f"  Duplicate seat: workspace {group['company_id']}, account "
                f"{group['account_id']} — employee {extra} merged into {keeper}"
                + (" (dry run)" if dry_run else "")
            )
            if dry_run:
                merged += 1
                continue
            try:
                with transaction.atomic():
                    merge_seat(extra, keeper, references=references)
                merged += 1
            except DatabaseError as exc:
                log(f"  Could not merge employee {extra} into {keeper}: {exc}")
    return merged


def merge_seat(extra: int, keeper: int, *, references=None) -> None:
    """Move everything that points at roster row [extra] onto [keeper], and
    retire [extra]. Both must be seats of one person in one workspace."""
    references = references if references is not None else _references_to("b2b_employee")
    # A secondment behind the extra seat is over: the person is here anyway.
    execute(
        "UPDATE b2b_workspace_membership SET is_active = FALSE, ended_at = NOW(), "
        "updated_at = NOW() WHERE employee_id = %s AND is_active = TRUE",
        [extra],
    )
    for table, column in references:
        if (table, column) in _NOT_MOVED:
            continue
        if table == "b2b_employee":
            execute(
                f"UPDATE b2b_employee SET {column} = %s WHERE {column} = %s AND id <> %s",
                [keeper, extra, keeper],
            )
            continue
        _repoint(table, column, extra, keeper)
    # A device registered on the retired seat keeps ringing: whichever token
    # the kept seat lacks, it takes from the other.
    execute(
        """
        UPDATE b2b_employee k
           SET fcm_token = COALESCE(k.fcm_token, x.fcm_token),
               voip_token = COALESCE(k.voip_token, x.voip_token),
               updated_at = NOW()
          FROM b2b_employee x
         WHERE k.id = %s AND x.id = %s
        """,
        [keeper, extra],
    )
    execute(
        "UPDATE b2b_employee SET is_active = FALSE, fcm_token = NULL, voip_token = NULL, "
        "updated_at = NOW() WHERE id = %s",
        [extra],
    )


def _repoint(table: str, column: str, extra: int, keeper: int) -> None:
    """`UPDATE table SET column = keeper WHERE column = extra`, where a row
    the kept seat already has its own copy of — the same chat membership, the
    same task assignment, the same day's attendance — is dropped instead of
    tripping the unique index that says there can be only one."""
    try:
        with transaction.atomic():
            execute(
                f"UPDATE {table} SET {column} = %s WHERE {column} = %s", [keeper, extra]
            )
        return
    except IntegrityError:
        pass
    for row in fetch_all(
        f"SELECT ctid::text AS ctid FROM {table} WHERE {column} = %s", [extra]
    ):
        try:
            with transaction.atomic():
                execute(
                    f"UPDATE {table} SET {column} = %s WHERE ctid = %s::tid",
                    [keeper, row["ctid"]],
                )
        except IntegrityError:
            try:
                with transaction.atomic():
                    execute(f"DELETE FROM {table} WHERE ctid = %s::tid", [row["ctid"]])
            except DatabaseError:
                # Still pointing at the retired seat, which keeps its id —
                # shown as a former colleague, not lost.
                pass


def _references_to(target: str) -> list[tuple[str, str]]:
    """Every single-column foreign key onto [target], as (table, column)."""
    rows = fetch_all(
        """
        SELECT c.conrelid::regclass::text AS tbl, a.attname AS col
          FROM pg_constraint c
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.contype = 'f'
           AND c.confrelid = %s::regclass
           AND array_length(c.conkey, 1) = 1
         ORDER BY 1, 2
        """,
        [target],
    )
    return [(row["tbl"], row["col"]) for row in rows]


# ─── Is anything in it? ───────────────────────────────────────────────────────

def _work_tables() -> list[str]:
    """Every table carrying a `company_id` that is not bookkeeping."""
    rows = fetch_all(
        """
        SELECT DISTINCT table_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND column_name = 'company_id'
           AND table_name IN (
                 SELECT table_name FROM information_schema.tables
                  WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
               )
         ORDER BY 1
        """
    )
    return [row["table_name"] for row in rows if row["table_name"] not in _BOOKKEEPING]


def workspace_has_work(company_id: int, *, tables: list[str] | None = None) -> bool:
    """Whether anybody has done anything in this workspace — written a
    task, a message, a lead, a note; stocked a product; clocked in."""
    for table in tables if tables is not None else _work_tables():
        if fetch_one(f"SELECT 1 AS hit FROM {table} WHERE company_id = %s LIMIT 1", [company_id]):
            return True
    # Its chat rooms are bookkeeping, but not what was said in them.
    return bool(
        fetch_one(
            """
            SELECT 1 AS hit
              FROM b2b_chat_message m
              JOIN b2b_chat_thread t ON t.id = m.thread_id
             WHERE t.company_id = %s
             LIMIT 1
            """,
            [company_id],
        )
    )


# ─── One company, two workspaces of one name ──────────────────────────────────

def retire_duplicate_workspaces(
    *, dry_run: bool = False, log: Log = logger.info
) -> tuple[int, int]:
    """Returns (retired, left for a person)."""
    rows = fetch_all(
        "SELECT id, org_id, name FROM b2b_company "
        "WHERE is_active = TRUE AND org_id IS NOT NULL ORDER BY org_id, id"
    )
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["org_id"], name_key(row["name"]))].append(row)
    groups = {key: value for key, value in groups.items() if len(value) > 1}
    if not groups:
        return 0, 0

    tables = _work_tables()
    retired = left = 0
    for (org_id, _key), copies in groups.items():
        used = [c for c in copies if workspace_has_work(c["id"], tables=tables)]
        keeper = used[0] if used else copies[0]
        for copy in copies:
            if copy["id"] == keeper["id"]:
                continue
            if copy in used or _seats_anyone_else(copy["id"], keeper["id"]):
                left += 1
                log(
                    f"  Same-named workspaces {keeper['id']} and {copy['id']} "
                    f"(\"{copy['name']}\", company {org_id}) are not a plain copy — "
                    f"both hold work or different people; left as they are"
                )
                continue
            log(
                f"  Duplicate workspace {copy['id']} (\"{copy['name']}\", company {org_id}) "
                f"retired in favour of {keeper['id']}" + (" (dry run)" if dry_run else "")
            )
            retired += 1
            if not dry_run:
                with transaction.atomic():
                    retire_workspace(copy["id"])
    return retired, left


def _seats_anyone_else(copy_id: int, keeper_id: int) -> bool:
    """Whether the copy has somebody on it who is not also in the workspace
    that stays. Retiring it would take their only way into that room — and
    two different people opening the same name is not a double tap."""
    return bool(
        fetch_one(
            """
            SELECT 1 AS hit
              FROM b2b_employee e
             WHERE e.company_id = %s AND e.is_active = TRUE AND e.is_chat_only = FALSE
               AND NOT EXISTS (
                     SELECT 1 FROM b2b_employee k
                      WHERE k.company_id = %s AND k.is_active = TRUE
                        AND k.account_id = e.account_id
                   )
             LIMIT 1
            """,
            [copy_id, keeper_id],
        )
    )


def retire_workspace(company_id: int) -> None:
    """Close an empty workspace, the way deleting one closes it.

    Its seats are retired with it, not moved to the copy that stays: the
    person who opened the copy is its admin, and carrying that seat across
    would make them an admin of somebody else's room. The callers only
    retire a copy whose people all sit in the one that stays."""
    execute(
        "UPDATE b2b_workspace_membership SET is_active = FALSE, ended_at = NOW(), "
        "updated_at = NOW() WHERE company_id = %s AND is_active = TRUE",
        [company_id],
    )
    execute(
        "UPDATE b2b_employee SET is_active = FALSE, updated_at = NOW() "
        "WHERE company_id = %s AND is_active = TRUE",
        [company_id],
    )
    execute(
        "UPDATE b2b_company SET is_active = FALSE, updated_at = NOW() WHERE id = %s",
        [company_id],
    )


# ─── One owner, two companies of one name ─────────────────────────────────────

def retire_duplicate_companies(
    *, dry_run: bool = False, log: Log = logger.info
) -> tuple[int, int]:
    """Returns (retired, left for a person).

    Two companies are copies when they have the same name *and* the same
    owners. A company is retired only when none of its workspaces holds work
    and nobody but its owners was ever let in."""
    orgs = fetch_all(
        """
        SELECT o.id, o.name,
               array_agg(DISTINCT e.account_id) FILTER (
                   WHERE e.role = 'owner' AND e.account_id IS NOT NULL
               ) AS owners,
               COUNT(e.id) FILTER (
                   WHERE e.role <> 'owner' AND e.is_chat_only = FALSE
               ) AS others
          FROM b2b_org o
          JOIN b2b_company c ON c.org_id = o.id AND c.is_active = TRUE
          LEFT JOIN b2b_employee e ON e.company_id = c.id AND e.is_active = TRUE
         WHERE o.is_active = TRUE
         GROUP BY o.id, o.name
         ORDER BY o.id
        """
    )
    groups: dict[tuple[str, tuple[int, ...]], list[dict[str, Any]]] = defaultdict(list)
    for org in orgs:
        owners = tuple(sorted(a for a in (org["owners"] or []) if a is not None))
        if owners:
            groups[(name_key(org["name"]), owners)].append(org)
    groups = {key: value for key, value in groups.items() if len(value) > 1}
    if not groups:
        return 0, 0

    tables = _work_tables()
    retired = left = 0
    for _key, copies in groups.items():
        workspaces = {
            org["id"]: [
                row["id"]
                for row in fetch_all(
                    "SELECT id FROM b2b_company WHERE org_id = %s AND is_active = TRUE",
                    [org["id"]],
                )
            ]
            for org in copies
        }

        def used(org) -> bool:
            return bool(org["others"]) or any(
                workspace_has_work(cid, tables=tables) for cid in workspaces[org["id"]]
            )

        in_use = [org for org in copies if used(org)]
        keeper = in_use[0] if in_use else copies[0]
        for org in copies:
            if org["id"] == keeper["id"]:
                continue
            if org in in_use:
                left += 1
                log(
                    f"  Same-named companies {keeper['id']} and {org['id']} "
                    f"(\"{org['name']}\") both hold work — left as they are"
                )
                continue
            log(
                f"  Duplicate company {org['id']} (\"{org['name']}\") retired in favour of "
                f"{keeper['id']}" + (" (dry run)" if dry_run else "")
            )
            retired += 1
            if dry_run:
                continue
            with transaction.atomic():
                for company_id in workspaces[org["id"]]:
                    retire_workspace(company_id)
                execute(
                    "UPDATE b2b_org SET is_active = FALSE, updated_at = NOW() WHERE id = %s",
                    [org["id"]],
                )
    return retired, left
