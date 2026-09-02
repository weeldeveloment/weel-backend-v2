"""Reading and writing the access model: roles, module access, permissions.

Its own module for the same reason `secondment_repository` is: this is the
table that decides what everybody else may do, and "where can access be
changed?" should have one file as its answer.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from django.utils import timezone
from django.utils.translation import gettext as _

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import (
    B2B_COMPANY_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_OWNERSHIP_REQUEST_TABLE,
)
from apps.b2b.workspace.access import (
    Module,
    Permission,
    Role,
    default_access,
    resolve,
)

B2B_WORKSPACE_ROLE_TABLE = "b2b_workspace_role"
B2B_AUDIT_EVENT_TABLE = "b2b_audit_event"


# ─── What each role may do, in one workspace ──────────────────────────────────

def list_role_config(company_id: int) -> dict[str, dict[str, list[str]]]:
    """Every role's configured access, keyed by role code.

    A role with no row is simply absent — [role_access] falls back to the
    catalogue's defaults for it. Nothing has to be seeded for a workspace to
    work, which is what keeps a new workspace from depending on a migration
    having run.
    """
    rows = fetch_all(
        f"SELECT code, modules, permissions FROM {B2B_WORKSPACE_ROLE_TABLE} "
        f"WHERE company_id = %s",
        [company_id],
    )
    return {
        row["code"]: {
            "modules": list(row["modules"] or []),
            "permissions": list(row["permissions"] or []),
        }
        for row in rows
        if row["code"] in Role.CHOICES
    }


def role_access(company_id: int, role: str) -> tuple[list[str], list[str]]:
    """One role's modules and permissions in this workspace, configured or not."""
    code = Role.clean(role)
    row = fetch_one(
        f"SELECT modules, permissions FROM {B2B_WORKSPACE_ROLE_TABLE} "
        f"WHERE company_id = %s AND code = %s",
        [company_id, code],
    )
    if not row:
        return default_access(code)
    return (
        Module.clean(row["modules"] or []),
        Permission.clean(row["permissions"] or []),
    )


def set_role_access(
    company_id: int,
    role: str,
    *,
    modules: Sequence[str],
    permissions: Sequence[str],
    actor_employee_id: int | None,
) -> dict[str, list[str]]:
    """Change what a role may do, for this workspace only.

    Upsert rather than insert-or-update in two statements: two administrators
    saving the role editor at the same moment would otherwise both find no row
    and both insert one.
    """
    code = Role.clean(role)
    clean_modules = Module.clean(modules)
    # Stored already narrowed to the modules the role holds. Keeping a
    # permission for a module the role cannot open would be storing something
    # that can never take effect, and it reads as a grant when the role editor
    # is next opened.
    _, clean_permissions = resolve(
        role=code, role_modules=clean_modules, role_permissions=Permission.clean(permissions)
    )
    now = timezone.now()
    execute(
        f"""
        INSERT INTO {B2B_WORKSPACE_ROLE_TABLE}
            (company_id, code, modules, permissions, updated_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, code) DO UPDATE
           SET modules = EXCLUDED.modules,
               permissions = EXCLUDED.permissions,
               updated_by = EXCLUDED.updated_by,
               updated_at = EXCLUDED.updated_at
        """,
        [
            company_id,
            code,
            json.dumps(clean_modules),
            json.dumps(clean_permissions),
            actor_employee_id,
            now,
            now,
        ],
    )
    record_audit(
        company_id,
        actor_employee_id=actor_employee_id,
        action="role.access_changed",
        target_type="role",
        payload={"role": code, "modules": clean_modules, "permissions": clean_permissions},
    )
    return {"modules": clean_modules, "permissions": clean_permissions}


# ─── One person's own access ──────────────────────────────────────────────────

def access_for_employee(employee: dict[str, Any]) -> tuple[list[str], list[str]]:
    """What this person can open and do, all four inputs folded together.

    Read on every authenticated request, so it is one indexed lookup — the
    role row — and nothing else. The overrides already travel on the employee
    row the authenticator has in hand.
    """
    # Somebody invited to one conversation is not a member of the workspace.
    # Nothing is open to them and nothing is permitted; the chat they were
    # invited to is decided by chat membership, not by this.
    if employee.get("is_chat_only"):
        return [], []

    role = Role.clean(employee.get("role"))
    role_modules, role_permissions = role_access(employee["company_id"], role)
    return resolve(
        role=role,
        role_modules=role_modules,
        role_permissions=role_permissions,
        module_override=employee.get("module_access"),
        permission_override=employee.get("permission_access"),
    )


def list_employee_invite_recipients(company_id: int) -> list[dict[str, Any]]:
    """Everyone this workspace lets decide a join request, with their push token.

    Resolved per employee rather than by role alone: a workspace that has
    handed ``employees.invite`` to a role other than owner/admin, or narrowed
    it away from one of them, gets exactly the audience its own role editor
    set — the same rule [access_for_employee] enforces on the endpoints
    themselves.
    """
    rows = fetch_all(
        f"SELECT * FROM {B2B_EMPLOYEE_TABLE} "
        f"WHERE company_id = %s AND is_active = TRUE AND is_hidden = FALSE",
        [company_id],
    )
    recipients = []
    for row in rows:
        _modules, permissions = access_for_employee(row)
        if Permission.EMPLOYEE_INVITE in permissions:
            recipients.append({
                "employee_id": row["id"],
                "company_id": row["company_id"],
                "fcm_token": row.get("fcm_token"),
            })
    return recipients


#: "This field was not sent", which is not the same as `None`. `None` means
#: "by role" and clears an override; this leaves whatever is stored alone.
KEEP = object()


def set_employee_access(
    employee_id: int,
    *,
    modules=KEEP,
    permissions=KEEP,
    company_id: int,
    actor_employee_id: int | None,
) -> None:
    """Give one person access that differs from their role's.

    `None` means "by role" — the ordinary case, and what clears an override
    that is no longer wanted. The TZ is explicit that individual access must
    not create a new role, which is exactly what storing it on the person
    rather than in the role table achieves.

    Each field is applied on its own, so changing somebody's modules does not
    quietly reset the permissions somebody else set last week.
    """
    sets, params = [], []
    if modules is not KEEP:
        sets.append("module_access = %s")
        params.append(json.dumps(Module.clean(modules)) if modules is not None else None)
    if permissions is not KEEP:
        sets.append("permission_access = %s")
        params.append(
            json.dumps(Permission.clean(permissions)) if permissions is not None else None
        )
    if not sets:
        return

    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET {', '.join(sets)}, updated_at = %s WHERE id = %s",
        [*params, timezone.now(), employee_id],
    )
    record_audit(
        company_id,
        actor_employee_id=actor_employee_id,
        action="employee.access_changed",
        target_type="employee",
        target_id=employee_id,
        payload={
            "modules": None
            if modules is None
            else (Module.clean(modules) if modules is not KEEP else "unchanged"),
            "permissions": None
            if permissions is None
            else (
                Permission.clean(permissions)
                if permissions is not KEEP
                else "unchanged"
            ),
        },
    )


#: Outranks-who, for the TZ's "only a lower role" rows (removing a member,
#: reassigning a role). Higher number outranks lower.
ROLE_RANK: dict[str, int] = {
    Role.OWNER: 4,
    Role.ADMIN: 3,
    Role.MANAGER: 2,
    Role.EMPLOYEE: 1,
    Role.GUEST: 0,
}


def outranks(actor_role: str, target_role: str) -> bool:
    return ROLE_RANK.get(Role.clean(actor_role), 0) > ROLE_RANK.get(
        Role.clean(target_role), 0
    )


def remove_employee(
    employee_id: int,
    *,
    company_id: int,
    scope: str,
    actor_employee_id: int | None,
) -> bool:
    """Ends a member's standing rather than deleting the row — their tasks,
    leads and history all still name somebody.

    ``scope="workspace"`` ends only this row. ``scope="company"`` ends every
    row the same phone number holds across every workspace under this one's
    org — the TZ's separate "remove from the organisation" row in the rights
    matrix, not just this one workspace.
    """
    target = fetch_one(
        f"SELECT phone FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s AND company_id = %s",
        [employee_id, company_id],
    )
    if not target:
        return False

    now = timezone.now()
    if scope == "company":
        org = fetch_one(f"SELECT org_id FROM {B2B_COMPANY_TABLE} WHERE id = %s", [company_id])
        org_id = org["org_id"] if org else None
        if org_id:
            execute(
                f"""
                UPDATE {B2B_EMPLOYEE_TABLE} SET is_active = FALSE, updated_at = %s
                 WHERE phone = %s AND company_id IN (
                     SELECT id FROM {B2B_COMPANY_TABLE} WHERE org_id = %s
                 )
                """,
                [now, target["phone"], org_id],
            )
        else:
            # No org above this workspace: "the company" and "this workspace"
            # are the same thing, so the narrower update already covers it.
            execute(
                f"UPDATE {B2B_EMPLOYEE_TABLE} SET is_active = FALSE, updated_at = %s "
                f"WHERE id = %s AND company_id = %s",
                [now, employee_id, company_id],
            )
    else:
        execute(
            f"UPDATE {B2B_EMPLOYEE_TABLE} SET is_active = FALSE, updated_at = %s "
            f"WHERE id = %s AND company_id = %s",
            [now, employee_id, company_id],
        )

    record_audit(
        company_id,
        actor_employee_id=actor_employee_id,
        action=f"employee.removed_from_{scope}",
        target_type="employee",
        target_id=employee_id,
    )
    return True


def set_employee_role(
    employee_id: int,
    role: str,
    *,
    company_id: int,
    actor_employee_id: int | None,
) -> None:
    from apps.b2b.workspace.roles import to_storage

    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET role = %s, updated_at = %s WHERE id = %s",
        [to_storage(role), timezone.now(), employee_id],
    )
    record_audit(
        company_id,
        actor_employee_id=actor_employee_id,
        action="employee.role_changed",
        target_type="employee",
        target_id=employee_id,
        # The canonical name, not the storage value — this is what the role
        # editor and the audit log's reader both speak.
        payload={"role": Role.clean(role)},
    )


# ─── Audit ────────────────────────────────────────────────────────────────────

def record_audit(
    company_id: int,
    *,
    actor_employee_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    payload: dict | None = None,
) -> None:
    """Append one line to the workspace's audit log.

    Never raises. An audit row that cannot be written is worth a log line and
    not worth failing the action that was already taken — the alternative is a
    role change that half happened.
    """
    import logging

    try:
        execute(
            f"INSERT INTO {B2B_AUDIT_EVENT_TABLE} "
            f"(company_id, actor_employee_id, action, target_type, target_id, payload, created_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                company_id,
                actor_employee_id,
                action,
                target_type,
                target_id,
                json.dumps(payload or {}),
                timezone.now(),
            ],
        )
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "Could not write the audit row for %s", action
        )


def list_audit(company_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT a.*, e.full_name AS actor_name
          FROM {B2B_AUDIT_EVENT_TABLE} a
          LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.actor_employee_id
         WHERE a.company_id = %s
         ORDER BY a.created_at DESC
         LIMIT %s
        """,
        [company_id, limit],
    )


# ─── Handing over or closing a company ─────────────────────────────────────────
#
# Neither move is ever a plain write from `apps.b2b.workspace` — see the note
# in `create_b2b_tables.py`. Everything here either raises a request for WEEL
# staff to decide, or — from `decide_ownership_request` — carries out what an
# `admin_auth` reviewer approved. Nothing in between; there is no endpoint
# that flips `status` without also being the thing that acted on it.

class OwnershipRequestKind:
    TRANSFER = "transfer"
    CLOSE = "close"


class OwnershipRequestStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OwnershipRequestError(Exception):
    """Raised for anything the view should answer 400 for. The message is
    already in the workspace's language — see the call sites in
    `access_views.py`."""


def pending_ownership_request(company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT r.*, t.full_name AS target_name
          FROM {B2B_OWNERSHIP_REQUEST_TABLE} r
          LEFT JOIN {B2B_EMPLOYEE_TABLE} t ON t.id = r.target_employee_id
         WHERE r.company_id = %s AND r.status = %s
        """,
        [company_id, OwnershipRequestStatus.PENDING],
    )


def list_own_ownership_requests(
    company_id: int, *, limit: int = 20
) -> list[dict[str, Any]]:
    """What the owner who filed these sees — most recent first, decided or
    not. Read only: this is history, not the queue [list_pending_ownership_requests]
    is."""
    return fetch_all(
        f"""
        SELECT r.*, t.full_name AS target_name
          FROM {B2B_OWNERSHIP_REQUEST_TABLE} r
          LEFT JOIN {B2B_EMPLOYEE_TABLE} t ON t.id = r.target_employee_id
         WHERE r.company_id = %s
         ORDER BY r.created_at DESC
         LIMIT %s
        """,
        [company_id, limit],
    )


def create_ownership_request(
    *,
    company_id: int,
    requested_by: int,
    kind: str,
    target_employee_id: int | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Raise a request to hand the company over or close it.

    Validated here rather than trusted from the caller, because this is the
    one function anything reaching this table goes through: a bad request
    reaching the admin queue would be WEEL staff's problem to notice instead
    of the workspace's.
    """
    if kind not in (OwnershipRequestKind.TRANSFER, OwnershipRequestKind.CLOSE):
        raise OwnershipRequestError(_("Not something that can be requested."))

    if pending_ownership_request(company_id):
        raise OwnershipRequestError(
            _("This workspace already has a request waiting for a decision.")
        )

    target = None
    if kind == OwnershipRequestKind.TRANSFER:
        if not target_employee_id:
            raise OwnershipRequestError(_("Choose who should become the owner."))
        target = fetch_one(
            f"SELECT id FROM {B2B_EMPLOYEE_TABLE} "
            f"WHERE id = %s AND company_id = %s AND is_active = TRUE "
            f"AND role <> %s",
            [target_employee_id, company_id, Role.OWNER],
        )
        if not target:
            raise OwnershipRequestError(
                _("That person is not on this workspace's roster.")
            )

    now = timezone.now()
    row = fetch_one(
        f"""
        INSERT INTO {B2B_OWNERSHIP_REQUEST_TABLE}
            (company_id, requested_by, kind, target_employee_id, reason,
             status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id,
            requested_by,
            kind,
            target["id"] if target else None,
            reason,
            OwnershipRequestStatus.PENDING,
            now,
            now,
        ],
    )
    record_audit(
        company_id,
        actor_employee_id=requested_by,
        action=f"ownership.{kind}_requested",
        target_type="ownership_request",
        target_id=row["id"],
    )
    return row


# ─── The admin_auth side ────────────────────────────────────────────────────

def list_pending_ownership_requests() -> list[dict[str, Any]]:
    """The queue — across every company, the way [WorkspaceTrashView]'s
    sibling on the support desk reads across every one too. `admin_auth` is
    WEEL's own desk, not any one workspace's."""
    return fetch_all(
        f"""
        SELECT r.*,
               c.name AS company_name,
               req.full_name AS requested_by_name,
               t.full_name AS target_name
          FROM {B2B_OWNERSHIP_REQUEST_TABLE} r
          JOIN {B2B_COMPANY_TABLE} c ON c.id = r.company_id
          LEFT JOIN {B2B_EMPLOYEE_TABLE} req ON req.id = r.requested_by
          LEFT JOIN {B2B_EMPLOYEE_TABLE} t ON t.id = r.target_employee_id
         WHERE r.status = %s
         ORDER BY r.created_at ASC
        """,
        [OwnershipRequestStatus.PENDING],
    )


def get_ownership_request(request_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_OWNERSHIP_REQUEST_TABLE} WHERE id = %s", [request_id]
    )


def decide_ownership_request(
    request_id: int,
    *,
    approve: bool,
    reviewer_user_id: int,
    note: str = "",
) -> dict[str, Any] | None:
    """Reject just closes the row. Approve carries out exactly what was asked
    — the transfer or the closure — before it does, so a request that is
    marked approved and a company that changed are the same fact, never two
    that could disagree after a crash between them.
    """
    request_row = get_ownership_request(request_id)
    if not request_row or request_row["status"] != OwnershipRequestStatus.PENDING:
        return None

    if approve:
        if request_row["kind"] == OwnershipRequestKind.TRANSFER:
            _execute_transfer(request_row)
        else:
            _execute_close(request_row)

    now = timezone.now()
    updated = fetch_one(
        f"""
        UPDATE {B2B_OWNERSHIP_REQUEST_TABLE}
           SET status = %s, review_note = %s, reviewed_by_user_id = %s,
               reviewed_at = %s, updated_at = %s
         WHERE id = %s
        RETURNING *
        """,
        [
            OwnershipRequestStatus.APPROVED if approve else OwnershipRequestStatus.REJECTED,
            note,
            reviewer_user_id,
            now,
            now,
            request_id,
        ],
    )
    record_audit(
        request_row["company_id"],
        actor_employee_id=None,
        action=f"ownership.{request_row['kind']}_{'approved' if approve else 'rejected'}",
        target_type="ownership_request",
        target_id=request_id,
    )
    return updated


def _execute_transfer(request_row: dict[str, Any]) -> None:
    """Moves the `owner` role from whoever asked to whoever they named.

    Re-checked against the roster as it stands now, not as it stood when the
    request was filed: the target may have left, or the requester may no
    longer hold the role somebody else already passed on. Either makes the
    request stale rather than wrong, so it is left pending-looking to the
    caller — [decide_ownership_request] still marks it approved, because
    silently downgrading an admin's decision to a no-op would be a worse
    surprise than a transfer that turns out to need re-requesting.
    """
    from apps.b2b.workspace.roles import to_storage

    company_id = request_row["company_id"]
    now = timezone.now()
    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET role = %s, updated_at = %s "
        f"WHERE id = %s AND company_id = %s AND is_active = TRUE",
        [to_storage(Role.MANAGER), now, request_row["requested_by"], company_id],
    )
    if request_row["target_employee_id"]:
        execute(
            f"UPDATE {B2B_EMPLOYEE_TABLE} SET role = %s, updated_at = %s "
            f"WHERE id = %s AND company_id = %s AND is_active = TRUE",
            [to_storage(Role.OWNER), now, request_row["target_employee_id"], company_id],
        )


def _execute_close(request_row: dict[str, Any]) -> None:
    """Closes the whole Company, not just the workspace the request came
    from — the owner's authority reaches every workspace under it, so what
    they may close does too. Mirrors the shutdown
    `accounts.companies_closed_by_deleting`'s caller performs, minus the
    parts that are about erasing a *person*: this is about a business
    deciding to stop, and nobody's roster row is touched by it.
    """
    company = fetch_one(
        f"SELECT org_id FROM {B2B_COMPANY_TABLE} WHERE id = %s",
        [request_row["company_id"]],
    )
    org_id = company.get("org_id") if company else None
    now = timezone.now()
    if org_id:
        execute(
            f"UPDATE {B2B_COMPANY_TABLE} SET is_active = FALSE, updated_at = %s "
            f"WHERE org_id = %s",
            [now, org_id],
        )
        execute(
            "UPDATE b2b_org SET is_active = FALSE, updated_at = %s WHERE id = %s",
            [now, org_id],
        )
    else:
        # No org above it — a workspace from before the org level existed.
        # Closing just this one is still the whole Company it is.
        execute(
            f"UPDATE {B2B_COMPANY_TABLE} SET is_active = FALSE, updated_at = %s "
            f"WHERE id = %s",
            [now, request_row["company_id"]],
        )


# ─── Deleting one workspace (TZ §4) ────────────────────────────────────────────
#
# Deliberately not the same table or flow as the ownership request above.
# That one hands the decision to WEEL staff because it can end the whole
# Company; this one never leaves the workspace — a leader asks, this same
# workspace's own owner decides, and approval touches only this workspace's
# `b2b_company` row. The org above it, and any other workspace under that
# org, is never reached from here.

B2B_WORKSPACE_DELETE_REQUEST_TABLE = "b2b_workspace_delete_request"


class WorkspaceDeleteStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def request_workspace_deletion(
    *, company_id: int, requested_by: int, reason: str = ""
) -> dict[str, Any]:
    existing = fetch_one(
        f"SELECT id FROM {B2B_WORKSPACE_DELETE_REQUEST_TABLE} "
        f"WHERE company_id = %s AND status = %s",
        [company_id, WorkspaceDeleteStatus.PENDING],
    )
    if existing:
        raise OwnershipRequestError(_("A deletion request is already pending."))

    now = timezone.now()
    row = fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_DELETE_REQUEST_TABLE}
            (company_id, requested_by, reason, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [company_id, requested_by, reason, WorkspaceDeleteStatus.PENDING, now, now],
    )
    record_audit(
        company_id,
        actor_employee_id=requested_by,
        action="workspace.delete_requested",
        target_type="workspace",
        target_id=company_id,
    )
    return row


def list_workspace_delete_requests(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_DELETE_REQUEST_TABLE} "
        f"WHERE company_id = %s ORDER BY created_at DESC",
        [company_id],
    )


def decide_workspace_deletion(
    request_id: int,
    *,
    company_id: int,
    approve: bool,
    reviewer_employee_id: int,
) -> dict[str, Any] | None:
    """Approve marks this workspace `is_active = FALSE` — nothing above it.
    Reject just closes the request."""
    row = fetch_one(
        f"SELECT status FROM {B2B_WORKSPACE_DELETE_REQUEST_TABLE} "
        f"WHERE id = %s AND company_id = %s",
        [request_id, company_id],
    )
    if not row or row["status"] != WorkspaceDeleteStatus.PENDING:
        return None

    now = timezone.now()
    if approve:
        execute(
            f"UPDATE {B2B_COMPANY_TABLE} SET is_active = FALSE, updated_at = %s WHERE id = %s",
            [now, company_id],
        )
    updated = fetch_one(
        f"""
        UPDATE {B2B_WORKSPACE_DELETE_REQUEST_TABLE}
           SET status = %s, decided_by = %s, updated_at = %s
         WHERE id = %s
        RETURNING *
        """,
        [
            WorkspaceDeleteStatus.APPROVED if approve else WorkspaceDeleteStatus.REJECTED,
            reviewer_employee_id,
            now,
            request_id,
        ],
    )
    record_audit(
        company_id,
        actor_employee_id=reviewer_employee_id,
        action=f"workspace.delete_{'approved' if approve else 'rejected'}",
        target_type="workspace",
        target_id=company_id,
    )
    return updated
