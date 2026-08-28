"""Reading and writing the access model: roles, module access, permissions.

Its own module for the same reason `secondment_repository` is: this is the
table that decides what everybody else may do, and "where can access be
changed?" should have one file as its answer.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import B2B_EMPLOYEE_TABLE
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


def set_employee_role(
    employee_id: int,
    role: str,
    *,
    company_id: int,
    actor_employee_id: int | None,
) -> None:
    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET role = %s, updated_at = %s WHERE id = %s",
        [Role.clean(role), timezone.now(), employee_id],
    )
    record_audit(
        company_id,
        actor_employee_id=actor_employee_id,
        action="employee.role_changed",
        target_type="employee",
        target_id=employee_id,
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
