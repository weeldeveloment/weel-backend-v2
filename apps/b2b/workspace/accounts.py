"""The Weel Account: one human, however many workspaces they work in.

The TZ's registration is `phone → OTP → name → username → account`, and only
then `join / create company` and `create / join workspace`. Everything before
that last line happens without the person belonging to anything — which is
precisely what the workspace session cannot express, because its token's
subject is a roster row.

So there are two sessions, and the difference is deliberate:

* an **account session** knows who you are and nothing about where you work.
  It can read your own profile, list the workspaces you belong to, accept an
  invitation, ask to join, and create a workspace. That is the whole list.
* a **workspace session** is what everything else needs. You get one by
  choosing a workspace, and it carries the employee row that every task,
  message and lead in the schema is written against.

Somebody who has just registered holds the first and none of the second, and
that is a legitimate state rather than a broken login.
"""
from __future__ import annotations

import json
import re
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import (
    B2B_ACCOUNT_TABLE,
    B2B_COMPANY_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_USER_SESSION_TABLE,
    B2B_USER_TABLE,
)


def digits(phone: str | None) -> str:
    """A phone number reduced to what actually identifies it.

    The same number is stored as "+998 90 123 45 67" in one place and
    "998901234567" in another, and the account index compares them this way —
    so every lookup has to as well, or a person registers twice.
    """
    return re.sub(r"[^0-9]", "", phone or "")


def get_account(account_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {B2B_ACCOUNT_TABLE} WHERE id = %s", [account_id])


def set_account_fcm_token(account_id: int, token: str | None) -> None:
    """Which phone to address this account by, before it is in a workspace.

    One token per account, the same way the roster holds one per employee: a
    token is per-install, so a phone signed into somebody else's account has
    to be able to claim it back.
    """
    execute(
        f"UPDATE {B2B_ACCOUNT_TABLE} SET fcm_token = %s, updated_at = %s WHERE id = %s",
        [token, timezone.now(), account_id],
    )


def clear_account_fcm_tokens(tokens: list[str]) -> None:
    """Drop the account tokens Firebase has just reported as dead.

    The account-side twin of `clear_employee_fcm_tokens`, and scoped to this
    table for the same reason: a token from an uninstalled app that is never
    cleared is re-sent to forever, and the default cleanup writes to a
    consumer table that never holds one of these.
    """
    if not tokens:
        return
    execute(
        f"UPDATE {B2B_ACCOUNT_TABLE} SET fcm_token = NULL, updated_at = %s "
        f"WHERE fcm_token = ANY(%s)",
        [timezone.now(), list(tokens)],
    )


def find_account_by_phone(phone: str) -> dict[str, Any] | None:
    suffix = digits(phone)
    if not suffix:
        return None
    return fetch_one(
        f"SELECT * FROM {B2B_ACCOUNT_TABLE} "
        f"WHERE regexp_replace(phone, '[^0-9]', '', 'g') = %s",
        [suffix],
    )


def ensure_account(phone: str, **profile) -> dict[str, Any] | None:
    """The account for this number, created if this is the first time.

    Registration and sign-in are the same call: a phone number that has never
    been seen becomes an account, and one that has been seen finds it. There
    is no separate "register" endpoint to get out of step with the login.
    """
    existing = find_account_by_phone(phone)
    if existing:
        return existing
    now = timezone.now()
    execute(
        f"INSERT INTO {B2B_ACCOUNT_TABLE} "
        f"(phone, first_name, last_name, photo, created_at, updated_at) "
        f"VALUES (%s, %s, %s, %s, %s, %s) "
        f"ON CONFLICT DO NOTHING",
        [
            phone,
            profile.get("first_name"),
            profile.get("last_name"),
            profile.get("photo"),
            now,
            now,
        ],
    )
    return find_account_by_phone(phone)


def full_name_from(
    first_name: str | None, last_name: str | None, fallback: str | None = None
) -> str | None:
    """"Karimov Aziz" — surname first, the way a name is written on a roster."""
    return " ".join(
        part for part in [last_name, first_name] if part
    ).strip() or fallback


def split_full_name(full_name: str | None) -> tuple[str, str]:
    """A written name back into (first, last), inverting [full_name_from].

    The first word is the surname and everything after it is the rest, which
    is what a two-field form needs to open with. Lossless for round-tripping —
    "Karimov Aziz Baxtiyorovich" comes back out unchanged — without pretending
    to know which of three words is the patronymic.
    """
    parts = (full_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[1:]), parts[0]


def update_account(account_id: int, **fields) -> dict[str, Any] | None:
    allowed = {
        key: value
        for key, value in fields.items()
        if key in {"first_name", "last_name", "photo", "username"}
    }
    if not allowed:
        return get_account(account_id)
    sets = ", ".join(f"{key} = %s" for key in allowed)
    execute(
        f"UPDATE {B2B_ACCOUNT_TABLE} SET {sets}, updated_at = %s WHERE id = %s",
        [*allowed.values(), timezone.now(), account_id],
    )
    return get_account(account_id)


#: The stickers a task or a message is answered with: seven, and the same
#: seven for everybody.
#:
#: Personal until 2026-09-09 — each account kept its own six, picked from a
#: catalogue on the profile screen — and one shared row again since. The
#: choice is gone, and the column it was kept in is no longer read: an account
#: that still carries an older six is not asked.
#:
#: 👎 is the addition, and it goes last: the six that were already there keep
#: the order people aim at without looking, and the new face is the seventh.
#:
#: Sent on `/me/` all the same rather than left to the app alone. A row of
#: emoji is exactly the sort of thing worth being able to change on every
#: phone at once, without waiting on a store release; the app holds the same
#: seven only as a fallback for a backend too old to send them.
DEFAULT_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👎"]


#: What a handle may look like — 3–50 characters, lowercase, starting with
#: a letter. Quoted here rather than in the serializer that enforces it so
#: that [suggest_usernames] cannot propose a name the serializer refuses.
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")


def username_taken(username: str, *, exclude_account_id: int | None = None) -> bool:
    """Whether this handle is somebody else's.

    Global, per the TZ: one person, one handle, wherever they work. Read
    before the write purely so the answer can be a sentence — the unique index
    is what actually decides.
    """
    sql = f"SELECT 1 AS taken FROM {B2B_ACCOUNT_TABLE} WHERE LOWER(username) = LOWER(%s)"
    params: list[Any] = [username]
    if exclude_account_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_account_id)
    return bool(fetch_one(sql, params))


def suggest_username(first_name: str | None, last_name: str | None, phone: str) -> str:
    """A free handle to offer, since the TZ says the system may propose one.

    Built from the name where there is one and from the number where there is
    not, then numbered until it is free. Bounded: after a few tries it falls
    back to the phone's digits, which cannot collide with anything a person
    would choose.
    """
    base = re.sub(r"[^a-z0-9]", "", (first_name or "").lower())[:20]
    if len(base) < 3:
        base = re.sub(r"[^a-z0-9]", "", (last_name or "").lower())[:20]
    if len(base) < 3:
        base = f"user{digits(phone)[-6:]}"
    if base and base[0].isdigit():
        base = f"u{base}"

    if not username_taken(base):
        return base
    for suffix in range(1, 50):
        candidate = f"{base}{suffix}"
        if not username_taken(candidate):
            return candidate
    return f"user{digits(phone)}"


def suggest_username_variants(base: str, limit: int = 3) -> list[str]:
    """Free handles that look like the one somebody just tried.

    Offered when the typed handle turns out to be taken. Suggestions built
    from the account's name are a different question — somebody who typed
    `xusan_design` has already decided what they want to be called, and
    answering with `xusangafurdjanov` ignores that.
    """
    base = re.sub(r"[^a-z0-9_]", "", base.lower())[:45]
    if not base:
        return []

    candidates = [f"{base}{suffix}" for suffix in range(1, 30)]
    candidates.append(f"{base}_uz")

    free: list[str] = []
    for candidate in candidates:
        if not USERNAME_RE.fullmatch(candidate):
            continue
        if not username_taken(candidate):
            free.append(candidate)
        if len(free) >= limit:
            break
    return free


def suggest_usernames(
    first_name: str | None,
    last_name: str | None,
    phone: str,
    limit: int = 3,
) -> list[str]:
    """A short list of free handles, not just one.

    The registration screen offers these as chips beside the field, and one
    suggestion is not an offer — somebody who does not like it is back to
    inventing a unique name against a rule they cannot see. Built from the
    parts of the name in the order a person would try them.
    """
    first = re.sub(r"[^a-z0-9]", "", (first_name or "").lower())[:20]
    last = re.sub(r"[^a-z0-9]", "", (last_name or "").lower())[:20]

    candidates: list[str] = []
    if first and last:
        candidates.append(f"{first}_{last[0]}")
        candidates.append(f"{first}{last}")
    if first:
        candidates.append(first)
    if last:
        candidates.append(last)
    # The numbered fallbacks, so a common first name still produces a list.
    for suffix in range(1, 20):
        if len(candidates) >= limit * 4:
            break
        candidates.append(f"{first or last or 'user'}{suffix}")

    free: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen or not USERNAME_RE.fullmatch(candidate):
            continue
        seen.add(candidate)
        if not username_taken(candidate):
            free.append(candidate)
        if len(free) >= limit:
            return free

    # Nothing built from the name was free — the phone cannot collide.
    if len(free) < limit:
        fallback = f"user{digits(phone)[-6:]}"
        if fallback not in seen and not username_taken(fallback):
            free.append(fallback)
    return free


# ─── Where this account works ─────────────────────────────────────────────────

def list_memberships(account_id: int) -> list[dict[str, Any]]:
    """Every workspace this account belongs to, with the standing it has there.

    Chat-only rows are left out: somebody invited to one conversation is not a
    member of the workspace, and offering it in the switcher would say they
    were.
    """
    return fetch_all(
        f"""
        SELECT e.id AS employee_id, e.role, e.is_guest, e.is_frozen, e.company_id,
               c.name AS company_name, c.slug AS company_slug, c.org_id,
               COALESCE(o.name, c.name) AS org_name
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
          LEFT JOIN b2b_org o ON o.id = c.org_id
         WHERE e.account_id = %s
           AND e.is_active = TRUE
           AND e.is_chat_only = FALSE
         ORDER BY o.name ASC NULLS LAST, e.is_guest ASC, c.name ASC
        """,
        [account_id],
    )


def employee_in_company(account_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_EMPLOYEE_TABLE} "
        f"WHERE account_id = %s AND company_id = %s AND is_active = TRUE "
        f"ORDER BY is_chat_only ASC, id ASC LIMIT 1",
        [account_id, company_id],
    )


def create_membership(
    *,
    account: dict[str, Any],
    company_id: int,
    role: str,
    modules=None,
    permissions=None,
    is_chat_only: bool = False,
) -> dict[str, Any] | None:
    """Put this account on a workspace's roster.

    The name, phone, photo and handle are copied from the account rather than
    joined at read time: every screen in the workspace reads `b2b_employee`,
    and a join on every roster query to fetch fields that change twice a year
    is a cost with nothing to show for it. The account stays the author — a
    later change to the name or the handle is propagated back down to every
    membership (see `set_own_profile` and `sync_username_across_memberships`).

    One seat per person per workspace. Somebody already on the roster is
    handed the seat they have — every door into a workspace (a link, a
    request, the owners seated on a new room) can reach a person who came in
    through another one, and a second row was a second copy of the
    workspace in their switcher. A chat-only or a borrowed (guest) seat is
    the exception: a real membership promotes that row rather than standing
    a second one beside it, so what they wrote there stays theirs.

    Checked under a lock on this person and this workspace, not by a unique
    index: the roster is linked to accounts by phone on every start-up
    (`create_b2b_tables`), and an index there would turn one old duplicate
    number into a container that never comes up.
    """
    from django.db import transaction

    from apps.b2b.workspace.access import Module, Permission, Role

    now = timezone.now()
    module_json = json.dumps(Module.clean(modules)) if modules is not None else None
    permission_json = (
        json.dumps(Permission.clean(permissions)) if permissions is not None else None
    )

    with transaction.atomic():
        _hold_lock(SEAT_LOCK_NAMESPACE, company_id, account["id"])
        existing = employee_in_company(account["id"], company_id)
        if existing:
            if is_chat_only or not (existing.get("is_chat_only") or existing.get("is_guest")):
                return existing
            if existing.get("is_guest"):
                # The secondment it came from is over the moment they are
                # staff here: ended the way `end_membership` ends one, minus
                # the part that retires the row.
                execute(
                    "UPDATE b2b_workspace_membership "
                    "SET is_active = FALSE, ended_at = %s, updated_at = %s "
                    "WHERE employee_id = %s AND is_active = TRUE",
                    [now, now, existing["id"]],
                )
            execute(
                f"""
                UPDATE {B2B_EMPLOYEE_TABLE}
                   SET is_chat_only = FALSE, is_guest = FALSE, is_hidden = FALSE,
                       home_employee_id = NULL, role = %s,
                       module_access = %s, permission_access = %s, updated_at = %s
                 WHERE id = %s
                """,
                [Role.clean(role), module_json, permission_json, now, existing["id"]],
            )
            return employee_in_company(account["id"], company_id)

        _insert_membership(
            account=account,
            company_id=company_id,
            role=Role.clean(role),
            module_json=module_json,
            permission_json=permission_json,
            is_chat_only=is_chat_only,
            now=now,
        )
    return employee_in_company(account["id"], company_id)


def person_seated_in(company_id: int, employee_id: int) -> bool:
    """Whether the person behind this roster row already has a seat in that
    workspace — through any row of theirs, not only this one.

    One account holds a row per workspace, so "is this row in B" says
    nothing about whether the *person* is: somebody hired into A and also
    on B's staff is asked for from B as "the A row", and lending them to B
    stood a guest copy of them beside their own seat there.
    """
    return bool(
        fetch_one(
            f"""
            WITH mine AS (
                SELECT x.id FROM {B2B_EMPLOYEE_TABLE} x WHERE x.id = %s
                UNION
                SELECT o.id
                  FROM {B2B_EMPLOYEE_TABLE} x
                  JOIN {B2B_EMPLOYEE_TABLE} o ON o.account_id = x.account_id
                 WHERE x.id = %s AND x.account_id IS NOT NULL
            )
            SELECT 1 AS seated
              FROM {B2B_EMPLOYEE_TABLE} seat
             WHERE seat.company_id = %s
               AND seat.is_active = TRUE
               AND seat.is_chat_only = FALSE
               AND (seat.id IN (SELECT id FROM mine)
                    OR seat.home_employee_id IN (SELECT id FROM mine))
             LIMIT 1
            """,
            [employee_id, employee_id, company_id],
        )
    )


#: First key of the advisory lock `create_membership` takes, so its locks
#: cannot collide with anybody else's two-key locks on the same numbers.
SEAT_LOCK_NAMESPACE = 710_000_000

#: The same for the lock `create_workspace` takes on the account opening one.
CREATE_LOCK_NAMESPACE = 720_000_000


def _hold_lock(namespace: int, scope: int | None, subject: int) -> None:
    """Serialise, until this transaction ends, everything else that takes
    the same lock — the check-then-insert of two taps a moment apart. A
    no-op outside PostgreSQL, which has no advisory locks and no second
    connection to race with."""
    from shared.raw.compat import is_postgresql

    if not is_postgresql():
        return
    execute(
        "SELECT pg_advisory_xact_lock(%s, %s)",
        [namespace + int(scope or 0) % 1_000_000, int(subject)],
    )


def _insert_membership(
    *,
    account: dict[str, Any],
    company_id: int,
    role: str,
    module_json: str | None,
    permission_json: str | None,
    is_chat_only: bool,
    now,
) -> None:
    full_name = full_name_from(
        account.get("first_name"), account.get("last_name"), account.get("phone")
    )

    execute(
        f"""
        INSERT INTO {B2B_EMPLOYEE_TABLE}
            (company_id, account_id, full_name, phone, photo, username, role,
             module_access, permission_access, is_active, is_chat_only,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
        """,
        [
            company_id,
            account["id"],
            full_name,
            account.get("phone"),
            account.get("photo"),
            account.get("username"),
            role,
            module_json,
            permission_json,
            is_chat_only,
            now,
            now,
        ],
    )


# ─── Adding people who are already in the company ────────────────────────────

def list_org_people_to_add(
    org_id: int | None,
    *,
    for_company_id: int | None = None,
    exclude_account_id: int | None = None,
    search: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Who in the company could be put on a workspace's roster.

    One row per *person*, not per seat: somebody who sits in three of the
    company's workspaces is one colleague to pick, and listing them three
    times would make the picker ask which copy — a question with no answer.
    The row handed back is their lowest-numbered seat; its id is what
    [add_org_member] takes, and the account behind it is what actually gets
    the membership.

    With [for_company_id], people already on that roster are left out —
    offering somebody who is there already is the "already a member" error
    one screen later. Guests and chat-only rows are left out too: a person
    lent into one of the company's rooms, or let into one conversation, is
    not the company's to hand around.
    """
    if org_id is None:
        return []
    params: list[Any] = [org_id]
    where = ""
    if exclude_account_id is not None:
        where += " AND (e.account_id IS NULL OR e.account_id <> %s)"
        params.append(exclude_account_id)
    if search:
        # The same search every other people picker has — a phone typed with
        # spaces, a name in either order (see `people_search`).
        from apps.b2b.workspace.people_search import people_search_clause

        clause, clause_params = people_search_clause(search)
        where += clause
        params += clause_params
    not_on = ""
    if for_company_id is not None:
        not_on = f"""
         WHERE NOT EXISTS (
                 SELECT 1 FROM {B2B_EMPLOYEE_TABLE} m
                  WHERE m.company_id = %s
                    AND m.is_active = TRUE
                    AND m.is_chat_only = FALSE
                    AND (
                          (p.account_id IS NOT NULL AND m.account_id = p.account_id)
                       OR (p.account_id IS NULL AND m.phone = p.phone)
                    )
               )
        """
        params.append(for_company_id)
    params.append(limit)
    return fetch_all(
        f"""
        SELECT p.id, p.account_id, p.full_name, p.username, p.position,
               p.phone, p.photo, p.role, p.company_id, p.company_name
          FROM (
            SELECT DISTINCT ON (person)
                   e.id, e.account_id, e.full_name, e.username, e.position,
                   e.phone, e.photo, e.role, e.company_id,
                   c.name AS company_name,
                   COALESCE(
                       'a:' || e.account_id::text,
                       'p:' || e.phone,
                       'e:' || e.id::text
                   ) AS person
              FROM {B2B_EMPLOYEE_TABLE} e
              JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
              LEFT JOIN {B2B_ACCOUNT_TABLE} a ON a.id = e.account_id
             WHERE c.org_id = %s
               AND c.is_active = TRUE
               AND e.is_active = TRUE
               AND e.is_guest = FALSE
               AND e.is_chat_only = FALSE
               AND e.is_hidden = FALSE
               {where}
             ORDER BY person, e.id ASC
          ) p
        {not_on}
         ORDER BY p.full_name ASC
         LIMIT %s
        """,
        params,
    )


def add_org_member(
    *,
    source_employee_id: int,
    company_id: int,
    role: str,
    modules=None,
    permissions=None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Put a colleague from elsewhere in the company on this workspace's roster.

    Answers `(employee, problem)`: the new roster row and `None`, or what
    stopped it — `not_found` (no such seat, or one in another company),
    `guest` (a lent or chat-only row, which is not the company's to add) or
    `already_member` (with the row that is already there).

    The person is added by the *account* behind the seat they were picked
    from, so what lands here is a membership of their own with the role and
    modules chosen for this room — never a copy of the standing they hold
    elsewhere. A hire the dashboard entered by hand, with no account yet, has
    nothing but the seat to go by and is copied by name and number, the way
    `ensure_workspace_employee` does for a login.
    """
    from apps.b2b.workspace.access import Module, Permission, Role

    source = fetch_one(
        f"""
        SELECT e.*, c.org_id
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
         WHERE e.id = %s AND e.is_active = TRUE
        """,
        [source_employee_id],
    )
    target = fetch_one(
        f"SELECT id, org_id FROM {B2B_COMPANY_TABLE} WHERE id = %s AND is_active = TRUE",
        [company_id],
    )
    if (
        not source
        or not target
        or source.get("org_id") is None
        or source["org_id"] != target["org_id"]
    ):
        return None, "not_found"
    if source.get("is_guest") or source.get("is_chat_only"):
        return None, "guest"

    account_id = source.get("account_id")
    if account_id is not None:
        existing = employee_in_company(account_id, company_id)
        if existing and not existing.get("is_chat_only"):
            return existing, "already_member"
        account = get_account(account_id)
        if not account:
            return None, "not_found"
        employee = create_membership(
            account=account,
            company_id=company_id,
            role=role,
            modules=modules,
            permissions=permissions,
        )
        return employee, None

    # No account behind the seat: the dashboard entered this person by hand.
    # The phone is the only thing that says who they are across rooms.
    existing = fetch_one(
        f"""
        SELECT * FROM {B2B_EMPLOYEE_TABLE}
         WHERE company_id = %s AND is_active = TRUE AND is_chat_only = FALSE
           AND phone IS NOT NULL AND phone = %s
         ORDER BY id ASC LIMIT 1
        """,
        [company_id, source.get("phone")],
    ) if source.get("phone") else None
    if existing:
        return existing, "already_member"
    now = timezone.now()
    employee = fetch_one(
        f"""
        INSERT INTO {B2B_EMPLOYEE_TABLE}
            (company_id, full_name, phone, email, position, photo, role,
             module_access, permission_access, is_active, is_chat_only,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, FALSE, %s, %s)
        __RETURNING_MARKER__
        """,
        [
            company_id,
            source.get("full_name"),
            source.get("phone"),
            source.get("email"),
            source.get("position"),
            source.get("photo"),
            Role.clean(role),
            json.dumps(Module.clean(modules)) if modules is not None else None,
            json.dumps(Permission.clean(permissions)) if permissions is not None else None,
            now,
            now,
        ],
    )
    if not employee:
        employee = fetch_one(
            f"SELECT * FROM {B2B_EMPLOYEE_TABLE} WHERE company_id = %s "
            f"ORDER BY id DESC LIMIT 1",
            [company_id],
        )
    return employee, None


# ─── Creating one ─────────────────────────────────────────────────────────────

def slugify_workspace(name: str) -> str:
    """A handle other people can type to find this workspace.

    Latin letters, digits and hyphens. Uzbek is written in Latin script here,
    so the name usually survives intact; anything that does not is dropped
    rather than transliterated, because a handle nobody can guess how to spell
    is no better than none.
    """
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return base or "workspace"


def free_workspace_slug(name: str) -> str:
    """The slug this workspace will actually get, numbered if it has to be."""
    from shared.raw.db import fetch_one as _fetch_one

    base = slugify_workspace(name)

    def taken(candidate: str) -> bool:
        return bool(
            _fetch_one(
                f"SELECT 1 AS t FROM {B2B_COMPANY_TABLE} WHERE LOWER(slug) = LOWER(%s)",
                [candidate],
            )
        )

    if not taken(base):
        return base
    for suffix in range(2, 200):
        candidate = f"{base}-{suffix}"
        if not taken(candidate):
            return candidate
    # Two hundred workspaces called the same thing is not a real case; the
    # unique index is still the authority if it ever happens.
    return f"{base}-{timezone.now().strftime('%H%M%S')}"


#: The alphabet a join code is drawn from. No `0`/`O` and no `1`/`I`: this is
#: read off one screen and typed into another, and those are the pairs people
#: get wrong.
JOIN_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

JOIN_CODE_PREFIX = "W-"


def normalise_join_code(code: str | None) -> str:
    """What somebody typed, reduced to what is actually compared.

    People paste the whole link, type it in lower case, and leave the "W-" off
    or put a space in the middle. All four are the same code.
    """
    text = (code or "").strip()
    if "/" in text:
        text = text.split("/")[-1]
    text = text.replace(" ", "").replace("-", "").upper()
    if text.startswith("W"):
        text = text[1:]
    return f"{JOIN_CODE_PREFIX}{text}" if text else ""


def _free_join_code() -> str:
    """A code no company holds yet."""
    import secrets

    for _ in range(20):
        body = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(5))
        candidate = f"{JOIN_CODE_PREFIX}{body}"
        if not fetch_one(
            "SELECT 1 AS t FROM b2b_org WHERE UPPER(join_code) = UPPER(%s)",
            [candidate],
        ):
            return candidate
    # Thirty-two to the fifth is thirty-three million; twenty misses means
    # something else is wrong, and the unique index is still the authority.
    return f"{JOIN_CODE_PREFIX}{secrets.token_hex(4).upper()}"


def find_org_by_join_code(code: str) -> dict[str, Any] | None:
    """The company a typed code names, or nothing.

    Deliberately says nothing about *why* it found nothing — a wrong code and
    a code for a closed company answer the same way. Guessing at five
    characters should not be able to tell the two apart.
    """
    normalised = normalise_join_code(code)
    if len(normalised) <= len(JOIN_CODE_PREFIX):
        return None
    return fetch_one(
        "SELECT * FROM b2b_org WHERE UPPER(join_code) = UPPER(%s) AND is_active = TRUE",
        [normalised],
    )


def org_workspaces_for_joining(
    org_id: int, *, account_id: int
) -> list[dict[str, Any]]:
    """Every room inside one company, for somebody standing outside it.

    Rows this account already holds a seat on are marked rather than hidden,
    and so is a request already waiting: somebody who has asked once and come
    back should be told that, not shown the same button again and answered
    with a 409.
    """
    return fetch_all(
        f"""
        SELECT c.id,
               c.name,
               c.slug,
               c.icon,
               (SELECT COUNT(*) FROM {B2B_EMPLOYEE_TABLE} e
                 WHERE e.company_id = c.id
                   AND e.is_active = TRUE
                   AND e.is_chat_only = FALSE) AS member_count,
               EXISTS (
                 SELECT 1 FROM {B2B_EMPLOYEE_TABLE} me
                  WHERE me.company_id = c.id
                    AND me.is_active = TRUE
                    AND me.account_id = %s
               ) AS is_member,
               EXISTS (
                 SELECT 1 FROM b2b_join_request j
                  WHERE j.company_id = c.id
                    AND j.account_id = %s
                    AND j.status = 'pending'
               ) AS has_pending_request
          FROM {B2B_COMPANY_TABLE} c
         WHERE c.org_id = %s AND c.is_active = TRUE
         ORDER BY c.name ASC
        """,
        [account_id, account_id, org_id],
    )


def org_owner_accounts(org_id: int | None) -> list[dict[str, Any]]:
    """The accounts that own this company: whoever holds an active owner seat
    in any of its workspaces. Read off the roster rather than
    ``b2b_org.owner_user_id`` — that column names a dashboard ``b2b_user``,
    which is a different identity from the account the app signs in with,
    and is NULL for every company opened from the app."""
    if org_id is None:
        return []
    return fetch_all(
        f"""
        SELECT DISTINCT a.*
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
          JOIN {B2B_ACCOUNT_TABLE} a ON a.id = e.account_id
         WHERE c.org_id = %s AND e.is_active = TRUE AND e.role = 'owner'
         ORDER BY a.id
        """,
        [org_id],
    )


def org_ids_for_account(account_id: int) -> list[int]:

    """The organisations this account already belongs to, through any roster
    row it holds."""
    rows = fetch_all(
        f"""
        SELECT DISTINCT c.org_id
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
         WHERE e.account_id = %s AND e.is_active = TRUE AND c.org_id IS NOT NULL
        """,
        [account_id],
    )
    return [row["org_id"] for row in rows]


def companies_closed_by_deleting(account_id: int) -> list[dict[str, Any]]:
    """The companies that would shut if this account went, and who is in them.

    A company's owner is the one standing no invitation and no request can
    hand out — see `Role`, and the two serializers that refuse it. Nothing in
    the app transfers it either, which is the whole reason this list exists:
    somebody who owns a company cannot hand it over first, so deleting their
    account has to say plainly what it closes.

    Only companies where this account is the *sole* owner are listed. One with
    a second owner carries on without this one.
    """
    return fetch_all(
        f"""
        SELECT o.id,
               o.name,
               (SELECT COUNT(*) FROM {B2B_EMPLOYEE_TABLE} m
                  JOIN {B2B_COMPANY_TABLE} mc ON mc.id = m.company_id
                 WHERE mc.org_id = o.id
                   AND m.is_active = TRUE
                   AND m.is_chat_only = FALSE
                   AND (m.account_id IS NULL OR m.account_id <> %s)
               ) AS other_members
          FROM b2b_org o
         WHERE o.is_active = TRUE
           AND EXISTS (
                 SELECT 1 FROM {B2B_EMPLOYEE_TABLE} e
                   JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
                  WHERE c.org_id = o.id
                    AND e.account_id = %s
                    AND e.is_active = TRUE
                    AND e.role = 'owner'
               )
           AND NOT EXISTS (
                 SELECT 1 FROM {B2B_EMPLOYEE_TABLE} e2
                   JOIN {B2B_COMPANY_TABLE} c2 ON c2.id = e2.company_id
                  WHERE c2.org_id = o.id
                    AND e2.is_active = TRUE
                    AND e2.role = 'owner'
                    AND (e2.account_id IS NULL OR e2.account_id <> %s)
               )
         ORDER BY o.name ASC
        """,
        [account_id, account_id, account_id],
    )


#: What a roster row says once the person behind it is gone.
#:
#: The row itself stays. Tasks, messages and leads are written against employee
#: ids, and deleting the row would either cascade half the workspace away or
#: leave dangling references nobody can render. A tombstone keeps the history
#: readable while carrying none of the person's details.
DELETED_MEMBER_NAME = "O'chirilgan foydalanuvchi"


def delete_account(account_id: int) -> dict[str, Any]:
    """Erase the person, keep the work.

    The order matters. Companies this account solely owns are closed first,
    because closing them reads their owner's roster row. Then every roster row
    is anonymised — the name, phone, email and photo the workspace kept a copy
    of — and deactivated. Then any legacy `b2b_user` login for this number is
    revoked and its phone released. Then the account row itself goes, which is
    what actually removes the phone number, the handle and the push token.

    `b2b_employee.account_id` is `ON DELETE SET NULL`, so the rows survive the
    last step on their own; they are anonymised first so that a crash between
    the two leaves tombstones rather than intact copies of somebody who asked
    to be forgotten.

    Roster rows are matched by phone as well as by `account_id`: a secondment
    carries a second row in another workspace, and a row created before the
    account was linked carries none of the link at all — leaving either behind
    with the number intact is what let a re-registration with the same phone
    walk straight back into the old workspace. The `b2b_user` sweep closes the
    same door for numbers that were ever a B2B owner or manager login: that
    table is consulted on sign-in (`_resolve_employee`) and would otherwise
    rebuild a roster row, with the old role, on the first code entered.
    """
    now = timezone.now()
    account = get_account(account_id)
    if account is None:
        return {"closed_companies": [], "seats_removed": 0}
    # The last nine digits, matched with a trailing `LIKE` — the exact rule
    # `find_b2b_user_by_phone` and `find_employee_by_phone` use on sign-in. The
    # cleanup has to reach every row those lookups could, or re-registration
    # walks back in through the one it missed.
    phone_digits = digits(account.get("phone"))
    phone_suffix = phone_digits[-9:] if len(phone_digits) >= 9 else phone_digits
    phone_like = f"%{phone_suffix}"

    closed = companies_closed_by_deleting(account_id)

    for org in closed:
        execute(
            f"UPDATE {B2B_COMPANY_TABLE} SET is_active = FALSE, updated_at = %s "
            f"WHERE org_id = %s",
            [now, org["id"]],
        )
        execute(
            "UPDATE b2b_org SET is_active = FALSE, updated_at = %s WHERE id = %s",
            [now, org["id"]],
        )

    seats = execute(
        f"""
        UPDATE {B2B_EMPLOYEE_TABLE}
           SET full_name = %s,
               phone = NULL,
               email = NULL,
               photo = NULL,
               username = NULL,
               fcm_token = NULL,
               -- The roster also keeps identity documents for the trips
               -- module. Leaving a passport number behind would make this a
               -- deletion in name only.
               date_of_birth = NULL,
               passport_series = NULL,
               passport_pinfl = NULL,
               passport_upload_front = NULL,
               passport_upload_back = NULL,
               is_active = FALSE,
               updated_at = %s
         WHERE account_id = %s
            OR (
                %s <> ''
                AND phone IS NOT NULL
                AND regexp_replace(phone, '[^0-9]', '', 'g') LIKE %s
            )
        """,
        [DELETED_MEMBER_NAME, now, account_id, phone_suffix, phone_like],
    )

    if phone_suffix:
        # Sessions first, while the number still matches, then the login row.
        execute(
            f"""
            DELETE FROM {B2B_USER_SESSION_TABLE}
             WHERE user_id IN (
                 SELECT id FROM {B2B_USER_TABLE}
                  WHERE regexp_replace(phone, '[^0-9]', '', 'g') LIKE %s
             )
            """,
            [phone_like],
        )
        # `b2b_user.phone` is `NOT NULL UNIQUE`, so it cannot be nulled the way
        # the roster's is — it is scrambled to a value that carries no real
        # digits, which both frees the number for a fresh registration and
        # stops `find_b2b_user_by_phone`'s suffix match from ever finding it.
        execute(
            f"""
            UPDATE {B2B_USER_TABLE}
               SET is_active = FALSE,
                   phone = 'deleted-' || id,
                   email = NULL,
                   first_name = NULL,
                   last_name = NULL,
                   updated_at = %s
             WHERE regexp_replace(phone, '[^0-9]', '', 'g') LIKE %s
            """,
            [now, phone_like],
        )

    execute(f"DELETE FROM {B2B_ACCOUNT_TABLE} WHERE id = %s", [account_id])
    return {
        "closed_companies": [org["name"] for org in closed],
        "seats_removed": seats or 0,
    }


def list_org_workspaces(org_id: int, *, account_id: int | None = None) -> list[dict[str, Any]]:
    """Every workspace under this company — not just the ones the caller
    happens to be on the roster of.

    What the "Workspace'lar" screen shows: an owner runs the whole company,
    not only the workspace they were hired into, and `WorkspaceOrgPeopleView`
    already treats an org's other workspaces as visible to anyone on one of
    them — this is the same boundary, one level up.

    With an [account_id] each row also carries what that account can *do*
    about a workspace it is not on: the `slug` a join request names, and
    whether one is already waiting. Without those the screen could list a
    room, refuse to open it, and offer nothing else — which is what it did.
    """
    return fetch_all(
        f"""
        SELECT c.id, c.name, c.slug, c.description, c.icon,
               (SELECT COUNT(*) FROM {B2B_EMPLOYEE_TABLE} m
                 WHERE m.company_id = c.id AND m.is_active = TRUE
                   AND m.is_chat_only = FALSE) AS member_count,
               admin.full_name AS admin_name,
               EXISTS (
                 SELECT 1 FROM b2b_join_request j
                  WHERE j.company_id = c.id
                    AND j.account_id = %s
                    AND j.status = 'pending'
               ) AS has_pending_request
          FROM {B2B_COMPANY_TABLE} c
          LEFT JOIN LATERAL (
                SELECT e.full_name
                  FROM {B2B_EMPLOYEE_TABLE} e
                 WHERE e.company_id = c.id AND e.is_active = TRUE
                   AND e.role IN ('owner', 'admin')
                 ORDER BY e.role = 'owner' DESC, e.id ASC
                 LIMIT 1
               ) admin ON TRUE
         WHERE c.org_id = %s AND c.is_active = TRUE
         ORDER BY c.name ASC
        """,
        [account_id, org_id],
    )


#: The apostrophes an Uzbek name is spelled with. "Sotuv bo'limi" and
#: "Sotuv boʻlimi" are one name typed on two keyboards.
_APOSTROPHES = str.maketrans({ch: "'" for ch in "‘’ʻʼ`"})


def name_key(name: str | None) -> str:
    """What two workspace (or company) names are compared by: case, the
    spacing and the apostrophe people happened to type all ignored."""
    return " ".join((name or "").translate(_APOSTROPHES).lower().split())


class NameTaken(Exception):
    """`create_workspace` refusing to open a second copy of something that
    already exists. `kind` is "workspace" (one inside the same company) or
    "company" (a company of the same name this account already owns)."""

    def __init__(self, kind: str, existing: dict[str, Any]):
        super().__init__(kind)
        self.kind = kind
        self.existing = existing


def same_named_workspace(org_id: int, name: str) -> dict[str, Any] | None:
    """A live workspace in this company that goes by this name already.

    Compared in Python rather than with SQL `LOWER`: under the C collation
    the database may run with, `LOWER` leaves Cyrillic alone, and a company
    has a handful of workspaces, not thousands."""
    key = name_key(name)
    rows = fetch_all(
        f"SELECT id, name, slug FROM {B2B_COMPANY_TABLE} "
        f"WHERE org_id = %s AND is_active = TRUE ORDER BY id",
        [org_id],
    )
    return next((row for row in rows if name_key(row["name"]) == key), None)


def same_named_company(account_id: int, name: str) -> dict[str, Any] | None:
    """A live company this account owns that goes by this name already —
    what a second tap on "Kompaniya yaratish" would otherwise open again."""
    key = name_key(name)
    rows = fetch_all(
        f"""
        SELECT DISTINCT o.id, o.name
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
          JOIN b2b_org o ON o.id = c.org_id
         WHERE e.account_id = %s AND e.is_active = TRUE AND e.role = 'owner'
           AND o.is_active = TRUE
         ORDER BY o.id
        """,
        [account_id],
    )
    return next((row for row in rows if name_key(row["name"]) == key), None)


def create_workspace(
    *,
    account: dict[str, Any],
    name: str,
    org_id: int | None = None,
    description: str | None = None,
    icon: str | None = None,
    workspace_name: str | None = None,
    tax_id: str | None = None,
) -> dict[str, Any] | None:
    """Open a new workspace, with this account on its roster.

    The TZ splits this in two and so does the standing it grants. Creating a
    workspace makes you its **admin** (§3) — you run that one workspace and
    nothing else. But somebody who belongs to no organisation yet is also
    creating the **company** that holds it, and the creator of a company is
    its owner.

    Two things follow from the owner holding the *company* rather than one
    room in it (TZ v2 §2, §11 "Владелец — все"):

    * an owner who opens another workspace is its owner, not demoted to its
      admin — §3's "the creator becomes the leader" is for people who are
      not already above that;
    * a workspace an admin opens still has the company's owner on it. Every
      owner-only act in a workspace — approving its deletion (§4), deciding
      what the admin role may do — needs an owner *in* it, and a room with
      nobody above the admin would be one nobody could ever close.

    Raises [NameTaken] rather than open a second of something that exists:
    a workspace named like one already in this company, or a company named
    like one this account already owns. Both are how the switcher came to
    list the same place twice — a slow answer tapped again, or one room
    opened by two people — and the check runs under a lock on the company
    (or, for a new one, on the account) so two taps cannot both pass it.
    Everything is written in one transaction: a failure half-way no longer
    leaves a company with no workspace, or a workspace with nobody in it.
    """
    from django.db import transaction

    name = (name or "").strip()
    with transaction.atomic():
        if org_id is None:
            _hold_lock(CREATE_LOCK_NAMESPACE, 0, account["id"])
            taken = same_named_company(account["id"], name)
            if taken:
                raise NameTaken("company", taken)
        else:
            _hold_lock(CREATE_LOCK_NAMESPACE, 1, org_id)
            taken = same_named_workspace(org_id, name)
            if taken:
                raise NameTaken("workspace", taken)
        return _open_workspace(
            account=account,
            name=name,
            org_id=org_id,
            description=description,
            icon=icon,
            workspace_name=workspace_name,
            tax_id=tax_id,
        )


def _open_workspace(
    *,
    account: dict[str, Any],
    name: str,
    org_id: int | None,
    description: str | None,
    icon: str | None,
    workspace_name: str | None,
    tax_id: str | None,
) -> dict[str, Any] | None:
    from apps.b2b.workspace.access import Role

    now = timezone.now()

    # The one thing that changes when there is no org yet: what gets named
    # what. `name` is what the person typed on the "Kompaniya yaratish"
    # screen, and it names the *company*, not this first workspace — a
    # brand-new company opens with a default workspace of its own,
    # "Sotuv bo'limi", the same way a fresh install of anything opens on
    # something rather than a blank list. Every workspace after this one is
    # opened by name, from inside the company; only the first is implicit.
    is_new_company = org_id is None
    org = None
    if is_new_company:
        org = fetch_one(
            "INSERT INTO b2b_org "
            "(name, tax_id, join_code, owner_user_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, NULL, %s, %s) __RETURNING_MARKER__",
            [name, (tax_id or "").strip() or None, _free_join_code(), now, now],
        )
        if not org:
            org = fetch_one(
                "SELECT * FROM b2b_org ORDER BY id DESC LIMIT 1"
            )
        org_id = org["id"] if org else None
        role = Role.OWNER
        # What the first workspace is called. The screen asks — "Birinchi
        # Workspace nomi" — and only falls back to a default when it is left
        # blank, which it may be: naming the company is the decision, and
        # naming the room it opens in is not one everybody has made yet.
        workspace_name = (workspace_name or "").strip() or "Sotuv bo'limi"
        workspace_description = None
        workspace_icon = "chart"
    else:
        owners = org_owner_accounts(org_id)
        role = (
            Role.OWNER
            if any(o["id"] == account["id"] for o in owners)
            else Role.ADMIN
        )
        workspace_name = name
        workspace_description = (description or "").strip() or None
        workspace_icon = icon


    company = fetch_one(
        f"""
        INSERT INTO {B2B_COMPANY_TABLE}
            (name, slug, org_id, description, icon, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s)
        __RETURNING_MARKER__
        """,
        [
            workspace_name,
            free_workspace_slug(workspace_name),
            org_id,
            workspace_description,
            workspace_icon,
            now,
            now,
        ],
    )
    if not company:
        company = fetch_one(
            f"SELECT * FROM {B2B_COMPANY_TABLE} WHERE org_id = %s ORDER BY id DESC LIMIT 1",
            [org_id],
        )
    if not company:
        return None

    employee = create_membership(
        account=account, company_id=company["id"], role=role
    )
    if not is_new_company:
        for owner in owners:
            if owner["id"] != account["id"]:
                create_membership(
                    account=owner, company_id=company["id"], role=Role.OWNER
                )
    if org is None and org_id is not None:

        org = fetch_one("SELECT * FROM b2b_org WHERE id = %s", [org_id])
    return {"company": company, "employee": employee, "role": role, "org": org}
