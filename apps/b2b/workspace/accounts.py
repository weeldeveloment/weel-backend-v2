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
        SELECT e.id AS employee_id, e.role, e.is_guest, e.company_id,
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

    The name and photo are copied from the account rather than joined at read
    time: every screen in the workspace reads `b2b_employee`, and a join on
    every roster query to fetch a name that changes twice a year is a cost
    with nothing to show for it. What the account owns is identity — the
    number and the handle — and those are read from it.
    """
    import json

    from apps.b2b.workspace.access import Module, Permission, Role

    now = timezone.now()
    full_name = full_name_from(
        account.get("first_name"), account.get("last_name"), account.get("phone")
    )

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

    Three things happen, and the order matters. Companies this account solely
    owns are closed first, because closing them reads their owner's roster
    row. Then every roster row is anonymised — the name, phone, email and
    photo the workspace kept a copy of — and deactivated. Then the account
    row itself goes, which is what actually removes the phone number, the
    handle and the push token.

    `b2b_employee.account_id` is `ON DELETE SET NULL`, so the rows survive the
    last step on their own; they are anonymised first so that a crash between
    the two leaves tombstones rather than intact copies of somebody who asked
    to be forgotten.
    """
    now = timezone.now()
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
        """,
        [DELETED_MEMBER_NAME, now, account_id],
    )

    execute(f"DELETE FROM {B2B_ACCOUNT_TABLE} WHERE id = %s", [account_id])
    return {
        "closed_companies": [org["name"] for org in closed],
        "seats_removed": seats or 0,
    }


def list_org_workspaces(org_id: int) -> list[dict[str, Any]]:
    """Every workspace under this company — not just the ones the caller
    happens to be on the roster of.

    What the "Workspace'lar" screen shows: an owner runs the whole company,
    not only the workspace they were hired into, and `WorkspaceOrgPeopleView`
    already treats an org's other workspaces as visible to anyone on one of
    them — this is the same boundary, one level up.
    """
    return fetch_all(
        f"""
        SELECT c.id, c.name, c.description, c.icon,
               (SELECT COUNT(*) FROM {B2B_EMPLOYEE_TABLE} m
                 WHERE m.company_id = c.id AND m.is_active = TRUE
                   AND m.is_chat_only = FALSE) AS member_count,
               admin.full_name AS admin_name
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
        [org_id],
    )


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
    workspace makes you its **admin** (§5) — you run that one workspace and
    nothing else. But somebody who belongs to no organisation yet is also
    creating the **company** that holds it (§4), and the creator of a company
    is its owner. So the first workspace somebody opens makes them an owner,
    and every one after that makes them an admin.
    """
    from apps.b2b.workspace.access import Role

    now = timezone.now()
    name = (name or "").strip()

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
        role = Role.ADMIN
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
    if org is None and org_id is not None:
        org = fetch_one("SELECT * FROM b2b_org WHERE id = %s", [org_id])
    return {"company": company, "employee": employee, "role": role, "org": org}
