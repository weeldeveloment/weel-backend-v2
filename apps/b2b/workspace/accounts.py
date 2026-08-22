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

import re
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import B2B_COMPANY_TABLE, B2B_EMPLOYEE_TABLE

B2B_ACCOUNT_TABLE = "b2b_account"


def digits(phone: str | None) -> str:
    """A phone number reduced to what actually identifies it.

    The same number is stored as "+998 90 123 45 67" in one place and
    "998901234567" in another, and the account index compares them this way —
    so every lookup has to as well, or a person registers twice.
    """
    return re.sub(r"[^0-9]", "", phone or "")


def get_account(account_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {B2B_ACCOUNT_TABLE} WHERE id = %s", [account_id])


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


# ─── Where this account works ─────────────────────────────────────────────────

def list_memberships(account_id: int) -> list[dict[str, Any]]:
    """Every workspace this account belongs to, with the standing it has there.

    Chat-only rows are left out: somebody invited to one conversation is not a
    member of the workspace, and offering it in the switcher would say they
    were.
    """
    return fetch_all(
        f"""
        SELECT e.id AS employee_id, e.role, e.is_guest, e.company_id,
               c.name AS company_name, c.slug AS company_slug, c.org_id
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
         WHERE e.account_id = %s
           AND e.is_active = TRUE
           AND e.is_chat_only = FALSE
         ORDER BY e.is_guest ASC, c.name ASC
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

    The name and photo are copied from the account rather than joined at read
    time: every screen in the workspace reads `b2b_employee`, and a join on
    every roster query to fetch a name that changes twice a year is a cost
    with nothing to show for it. What the account owns is identity — the
    number and the handle — and those are read from it.
    """
    import json

    from apps.b2b.workspace.access import Module, Permission, Role

    now = timezone.now()
    full_name = " ".join(
        part for part in [account.get("last_name"), account.get("first_name")] if part
    ).strip() or account.get("phone")

    execute(
        f"""
        INSERT INTO {B2B_EMPLOYEE_TABLE}
            (company_id, account_id, full_name, phone, photo, role,
             module_access, permission_access, is_active, is_chat_only,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
        """,
        [
            company_id,
            account["id"],
            full_name,
            account.get("phone"),
            account.get("photo"),
            Role.clean(role),
            json.dumps(Module.clean(modules)) if modules is not None else None,
            json.dumps(Permission.clean(permissions)) if permissions is not None else None,
            is_chat_only,
            now,
            now,
        ],
    )
    return employee_in_company(account["id"], company_id)


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


def create_workspace(
    *, account: dict[str, Any], name: str, org_id: int | None = None
) -> dict[str, Any] | None:
    """Open a new workspace, with this account on its roster.

    The TZ splits this in two and so does the standing it grants. Creating a
    workspace makes you its **admin** (§5) — you run that one workspace and
    nothing else. But somebody who belongs to no organisation yet is also
    creating the **company** that holds it (§4), and the creator of a company
    is its owner. So the first workspace somebody opens makes them an owner,
    and every one after that makes them an admin.
    """
    from apps.b2b.workspace.access import Role

    now = timezone.now()
    name = (name or "").strip()

    if org_id is None:
        org = fetch_one(
            "INSERT INTO b2b_org (name, owner_user_id, created_at, updated_at) "
            "VALUES (%s, NULL, %s, %s) __RETURNING_MARKER__",
            [name, now, now],
        )
        if not org:
            org = fetch_one(
                "SELECT * FROM b2b_org ORDER BY id DESC LIMIT 1"
            )
        org_id = org["id"] if org else None
        role = Role.OWNER
    else:
        role = Role.ADMIN

    company = fetch_one(
        f"""
        INSERT INTO {B2B_COMPANY_TABLE} (name, slug, org_id, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, TRUE, %s, %s)
        __RETURNING_MARKER__
        """,
        [name, free_workspace_slug(name), org_id, now, now],
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
    return {"company": company, "employee": employee, "role": role}
