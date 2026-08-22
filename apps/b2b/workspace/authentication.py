from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.request import Request
from shared.jwt_authentication import DenylistCheckedJWTAuthentication

from users.tokens import TokenMetadata

from apps.b2b.repository import get_b2b_user
from apps.b2b.workspace.repository import ensure_workspace_employee, get_workspace_employee
from apps.b2b.workspace.roles import capabilities_for, is_manager
from apps.b2b.workspace.tokens import WORKSPACE_ACCOUNT_TYPE, WORKSPACE_USER_TYPE

# The dashboard's own token type. Its subject is a ``b2b_user`` id, which is a
# different table from ``b2b_employee`` — the bridge below is the only place
# the two identities are allowed to meet.
DASHBOARD_USER_TYPE = "b2b"


class WorkspaceUser:
    """The signed-in employee, as seen by permissions and views."""

    is_authenticated = True
    is_active = True

    def __init__(self, employee: dict, membership=None):
        self._data = employee
        self.id = employee["id"]
        self.employee_id = employee["id"]
        self.company_id = employee["company_id"]
        self.role = employee.get("role") or "employee"
        self.full_name = employee.get("full_name")
        self.phone = employee.get("phone")

        #: Resolved lazily and once: most requests never ask, and the ones
        #: that do ask several times.
        self._access = None

        #: The secondment this identity *is*, or None for a permanent hire.
        #: Set for a guest row — somebody lent to this workspace by another —
        #: and what narrows [capabilities] below their role.
        self.membership = membership

    @property
    def is_guest(self) -> bool:
        return self.membership is not None

    @property
    def home_employee_id(self) -> int:
        """The row this person was hired into, which for a permanent employee
        is themselves. What identifies the *human* across workspaces."""
        return self.membership.home_employee_id if self.membership else self.id

    @property
    def modules(self) -> list[str] | None:
        """The modules a guest was granted; None for a permanent employee,
        who is answered by their role alone."""
        return list(self.membership.modules) if self.membership else None

    @property
    def is_manager(self) -> bool:
        return is_manager(self.role)

    @property
    def capabilities(self) -> dict[str, bool]:
        """The legacy flag map, computed from this person's real access.

        Not `capabilities_for(role)`: that one answers from the catalogue's
        defaults, and what the endpoints have to honour is what this workspace
        configured and what this person was individually granted. Both come out
        of [access].
        """
        from apps.b2b.workspace.access import capabilities_from

        modules, permissions = self.access
        return capabilities_from(self.role, modules, permissions)

    # ── The TZ's access model: who → where → what ────────────────────────────
    #
    # Alongside [capabilities] rather than replacing it. That map is what the
    # workspace's existing endpoints are gated on and what the app draws from
    # today; this is the finer-grained model the role editor writes and the
    # newer endpoints check. They will be one thing — see the note on
    # `access.py` — and until then the two are kept deliberately separate so
    # neither can silently widen the other.

    @property
    def access(self) -> tuple[list[str], list[str]]:
        """This person's modules and permissions, resolved once per request."""
        if self._access is None:
            from apps.b2b.workspace.access_repository import access_for_employee

            self._access = access_for_employee(self._data)
        return self._access

    @property
    def open_modules(self) -> list[str]:
        return self.access[0]

    @property
    def permissions(self) -> list[str]:
        return self.access[1]

    def may(self, permission: str) -> bool:
        """Whether this person holds one named permission.

        False for anything unknown, so a permission this build has not heard
        of — or one an older deployment does not grant — closes the door
        rather than opening it.
        """
        return permission in set(self.access[1])

    def opens(self, module: str) -> bool:
        from apps.b2b.workspace.access import Module

        return module in set(self.access[0]) or Module.ALIASES.get(module) in set(
            self.access[0]
        )

    @property
    def visible_scope(self) -> int | None:
        """Passed to the repository as ``visible_to``: ``None`` means "the
        whole company", an id means "only this person's own work"."""
        return None if self.is_manager else self.id

    @property
    def task_scope(self) -> int | None:
        """The same thing for tasks — and deliberately ``None`` for everyone.

        The board is the company's work, not a private list: an employee opens
        the tasks screen to see what the team is doing and narrows to their own
        with the app's "Menikilar" toggle, which is a client-side filter over
        this response. Writing is still gated by role (``can_edit_task`` and
        friends), so a wider read does not let an employee touch anything they
        could not touch before. Kept as its own property rather than dropping
        the argument at every call site, so the policy has one place to live —
        and so the calendar, which really is private per person, keeps using
        ``visible_scope``.
        """
        return None

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


def resolve_membership(employee: dict):
    """The live secondment behind a guest employee row, if it is still in force.

    Returns None for a permanent employee — the ordinary case, and one indexed
    lookup on a unique column, which is what makes it affordable on every
    request.

    A guest whose window has closed is refused outright rather than answered
    with a narrower permission map. Their access to this workspace ended; an
    access token minted before it did is still cryptographically valid, and
    the nightly sweep that retires the row may not have run yet, so this is
    the check that has to be the authority.
    """
    if not employee.get("is_guest"):
        return None

    from apps.b2b.workspace.secondment import Membership
    from apps.b2b.workspace.secondment_repository import membership_for_employee

    row = membership_for_employee(employee["id"])
    if not row:
        # A guest row with no secondment behind it should not exist. It is a
        # half-finished accept or a hand-edited table either way, and the safe
        # reading of "no grant" is "no access".
        raise exceptions.AuthenticationFailed(
            _("This workspace access has ended."), code="secondment_missing"
        )

    membership = Membership.from_row(row)
    if not membership.is_live:
        raise exceptions.AuthenticationFailed(
            _("This workspace access has ended."), code="secondment_expired"
        )
    return membership


class WorkspaceJWTAuthentication(DenylistCheckedJWTAuthentication):
    """Authenticates the B2B mobile app. Ignores every other token type so it
    can sit alongside the dashboard and client authenticators."""

    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)

        if validated_token.get(TokenMetadata.TOKEN_USER_TYPE) != WORKSPACE_USER_TYPE:
            return None

        subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
        try:
            employee_id = int(subject)
        except (TypeError, ValueError):
            raise exceptions.AuthenticationFailed(
                _("Invalid workspace token subject"), code="invalid_workspace_token"
            )

        # Re-read the row instead of trusting the token's claims: an employee
        # deactivated or demoted mid-session must lose access immediately, not
        # when their access token happens to expire.
        employee = get_workspace_employee(employee_id)
        if not employee:
            raise exceptions.AuthenticationFailed(
                _("Employee not found or deactivated"), code="employee_not_found"
            )

        return WorkspaceUser(employee, resolve_membership(employee)), validated_token


class DashboardWorkspaceAuthentication(DenylistCheckedJWTAuthentication):
    """Lets a **web dashboard** login act on the workspace endpoints.

    Tasks, chat, the calendar and the lead board are the same company's data
    whichever screen it is opened from, but the dashboard signs in as a
    ``b2b_user`` while every workspace row references ``b2b_employee(id)``.
    Rather than asking an owner to log in twice — once for the dashboard, once
    for the workspace — this resolves the dashboard account to the employee
    row that represents it, exactly as the workspace login does via
    ``_resolve_employee``, and hands the views the ``WorkspaceUser`` they
    already know how to reason about. Permissions are then unchanged: the
    role on the employee row decides what this person may do.

    Deliberately *not* in ``DEFAULT_AUTHENTICATION_CLASSES``: a dashboard token
    must keep resolving to ``B2BAuthUser`` everywhere else, or the company
    endpoints would start reading an employee id as a dashboard user id. It is
    attached only to ``WorkspaceAPIView``.
    """

    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)

        if validated_token.get(TokenMetadata.TOKEN_USER_TYPE) != DASHBOARD_USER_TYPE:
            return None

        subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
        try:
            user_id = int(subject)
        except (TypeError, ValueError):
            raise exceptions.AuthenticationFailed(
                _("Invalid B2B token subject"), code="invalid_b2b_token"
            )

        b2b_user = get_b2b_user(user_id)
        if not b2b_user:
            raise exceptions.AuthenticationFailed(
                _("B2B user not found"), code="b2b_user_not_found"
            )

        # An owner created by ``create_b2b_owner`` has no roster row until
        # something needs one; the dashboard opening the tasks page is such a
        # moment. Everyone else matches an existing employee by phone.
        employee = ensure_workspace_employee(b2b_user)
        if not employee:
            raise exceptions.AuthenticationFailed(
                _("No workspace employee for this account"), code="employee_not_found"
            )

        # `ensure_workspace_employee` resolves to a permanent row, so this is
        # None in practice — passed anyway so the two authenticators cannot
        # drift into disagreeing about what a guest is.
        return WorkspaceUser(employee, resolve_membership(employee)), validated_token


class WorkspaceAccount:
    """The signed-in Weel Account, before a workspace has been chosen.

    Not a [WorkspaceUser] and deliberately not compatible with one: it has no
    `company_id` and no role, because it has no workspace. Endpoints that take
    this principal are the handful that get somebody *into* a workspace — see
    `joining_views`.
    """

    is_authenticated = True
    is_active = True

    def __init__(self, account: dict):
        self._data = account
        self.id = account["id"]
        self.account_id = account["id"]
        self.phone = account.get("phone")
        self.username = account.get("username")

    @property
    def full_name(self) -> str:
        return " ".join(
            part
            for part in [self._data.get("last_name"), self._data.get("first_name")]
            if part
        ).strip()

    @property
    def has_profile(self) -> bool:
        """Whether registration finished. The TZ's flow is phone → OTP → name
        → username, and somebody who stopped after the OTP has an account with
        neither — which is what the app needs to know to resume them."""
        return bool(self._data.get("first_name")) and bool(self.username)

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


class AccountJWTAuthentication(DenylistCheckedJWTAuthentication):
    """Authenticates an account session. Ignores every other token type.

    Attached only to the joining endpoints. A workspace endpoint that
    accidentally accepted one of these would read an account id as an employee
    id — different table, same integers — so the type check is not a
    formality.
    """

    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None
        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        if validated_token.get(TokenMetadata.TOKEN_USER_TYPE) != WORKSPACE_ACCOUNT_TYPE:
            return None

        try:
            account_id = int(validated_token.get(TokenMetadata.TOKEN_SUBJECT))
        except (TypeError, ValueError):
            raise exceptions.AuthenticationFailed(
                _("Invalid account token subject"), code="invalid_account_token"
            )

        from apps.b2b.workspace.accounts import get_account

        account = get_account(account_id)
        if not account or not account.get("is_active", True):
            raise exceptions.AuthenticationFailed(
                _("Account not found"), code="account_not_found"
            )
        return WorkspaceAccount(account), validated_token
