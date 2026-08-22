"""Test bootstrap for the B2B suites.

These are unit tests: they build a `WorkspaceUser` from a dict and call a view
directly, with no database behind them. What each role may do is now read from
`b2b_workspace_role` — a workspace's own configuration — so without the fixture
below every one of them would reach for a connection that is not there.

Answering from the catalogue's defaults is the right stand-in: it is exactly
what an unconfigured workspace gets, which is the state almost every
deployment is in. The configured path is not skipped by this — it has its own
tests in `test_access.py`, which patch the repository deliberately.
"""
import pytest


@pytest.fixture(autouse=True)
def _access_from_defaults(request):
    """Resolve access without a database, unless the test asks for one."""
    # A test that patches the repository itself, or that runs against the real
    # schema, opts out by asking for the `db` fixture or by patching first.
    if "db" in request.fixturenames or "transactional_db" in request.fixturenames:
        yield
        return

    from unittest.mock import patch

    from apps.b2b.workspace.access import Role, default_access

    def _defaults(employee):
        if employee.get("is_chat_only"):
            return [], []
        from apps.b2b.workspace.access import resolve

        role = Role.clean(employee.get("role"))
        role_modules, role_permissions = default_access(role)
        return resolve(
            role=role,
            role_modules=role_modules,
            role_permissions=role_permissions,
            module_override=employee.get("module_access"),
            permission_override=employee.get("permission_access"),
        )

    with patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        side_effect=_defaults,
    ):
        yield
