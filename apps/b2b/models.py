from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from shared.models import HardDeleteBaseModel


class TripStatus:
    DRAFT = "draft"
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    CHOICES = [DRAFT, PENDING, ACTIVE, COMPLETED, CANCELLED]


class TripEmployeeStatus:
    INVITED = "invited"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"

    CHOICES = [INVITED, CONFIRMED, CHECKED_IN, CHECKED_OUT, CANCELLED]


class BudgetRequestStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    CHOICES = [PENDING, APPROVED, REJECTED]


class B2BUserRole:
    OWNER = "owner"
    PERFORMER = "performer"

    CHOICES = [OWNER, PERFORMER]


class EmployeeStatus:
    AVAILABLE = "available"
    ON_TRIP = "on_trip"
    BLOCKED = "blocked"

    CHOICES = [AVAILABLE, ON_TRIP, BLOCKED]


class EmployeeRole:
    """What somebody is on a workspace's roster.

    Four levels, and the two in the middle are not interchangeable:

    * ``OWNER``     — the company. One per workspace.
    * ``PERFORMER`` — the manager. Hands out work: raises leads, creates
      tasks, books the shared calendar. The app calls this one "Manager"; the
      column has said `performer` since the dashboard was written and renaming
      it would touch every row and every screen for a label.

      One per workspace among permanent staff, because this is also the
      employee who holds a dashboard login — see `B2BEmployeeCreateView`.
      Guests are not counted against that: somebody lent here for a fortnight
      is not this workspace's web login.
    * ``LIDER``     — a team lead. Many per workspace. Everything a manager
      can do, plus asking another workspace for help — which commits this one
      to letting an outsider in and is deliberately not a manager's call.
    * ``EMPLOYEE``  — works what they are given.
    """

    OWNER = "owner"
    PERFORMER = "performer"
    LIDER = "lider"
    EMPLOYEE = "employee"

    CHOICES = [OWNER, PERFORMER, LIDER, EMPLOYEE]


class LeadStatus:
    """A sales lead a manager posts to the workspace board for any employee
    to claim and work: company + contact + what they want to buy."""
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

    CHOICES = [NEW, IN_PROGRESS, COMPLETED]


class LeadStage:
    """Where a lead sits *inside* its status.

    ``LeadStatus`` is the board's three columns — unclaimed, somebody is on it,
    done. The stage is the step the salesperson actually moves through, and it
    is what the funnel screen shows on every card. The two are kept in step at
    one place only (``repository.set_lead_stage``): reaching ``WON`` or ``LOST``
    completes the lead, and nothing else changes the status.
    """
    NEW = "new"
    INTERESTED = "interested"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    #: Terms are agreed and the paperwork is being drawn up — the last step
    #: before a deal is actually won, and the one a salesperson sits in
    #: longest. Added after the funnel screen shipped, which is why it is at
    #: the end of the list and not in funnel order; ``ORDER`` is what orders it.
    CONTRACT = "contract"
    WON = "won"
    LOST = "lost"

    CHOICES = [NEW, INTERESTED, PROPOSAL, NEGOTIATION, CONTRACT, WON, LOST]

    #: The funnel's own order, which ``CHOICES`` cannot carry — a stage added
    #: later has to go on the end of the choices for the existing rows' sake.
    #: This is what "which stages come after this one" is read from.
    ORDER = [NEW, INTERESTED, PROPOSAL, NEGOTIATION, CONTRACT, WON, LOST]

    #: The stages that close a lead — reaching either sets the status to
    #: ``LeadStatus.COMPLETED``.
    CLOSED = [WON, LOST]


class LeadLostReason:
    """Why a deal was lost.

    A closed-lost lead without a reason is a number nobody can act on, so the
    stage change demands one. Kept as a short fixed list rather than free text
    for exactly that reason: five reasons that can be counted beat a thousand
    sentences that cannot. The salesperson's own words go in the note beside it.
    """
    PRICE = "price"
    COMPETITOR = "competitor"
    NO_BUDGET = "no_budget"
    NO_RESPONSE = "no_response"
    NOT_NEEDED = "not_needed"
    POSTPONED = "postponed"
    OTHER = "other"

    CHOICES = [PRICE, COMPETITOR, NO_BUDGET, NO_RESPONSE, NOT_NEEDED, POSTPONED, OTHER]


class LeadSource:
    """Where a lead came from. Reported on every card, so a company can see
    which channel is actually filling the funnel."""
    WEBSITE = "website"
    CALL = "call"
    REFERRAL = "referral"
    EXHIBITION = "exhibition"
    MANUAL = "manual"

    CHOICES = [WEBSITE, CALL, REFERRAL, EXHIBITION, MANUAL]


class LeadActivityKind:
    """One row of a lead's history.

    Everything but ``COMMENT`` is written by the server as a side effect of the
    action it names, which is why the text is composed there and not accepted
    from the client.
    """
    CREATED = "created"
    CLAIMED = "claimed"
    ASSIGNED = "assigned"
    STAGE = "stage"
    COMMENT = "comment"
    COMPLETED = "completed"

    CHOICES = [CREATED, CLAIMED, ASSIGNED, STAGE, COMMENT, COMPLETED]


class TaskActivityKind:
    """One row of a task's history.

    Everything but ``COMMENT`` is written by the server as a side effect of the
    action it names, which is why the text is composed there and not accepted
    from the client. Rows outlive the task itself (``task_id`` is nullable, see
    ``b2b_task_activity``) so a deleted task still reads as "X deleted" in the
    company-wide feed on the tasks page.
    """
    CREATED = "created"
    UPDATED = "updated"
    STATUS = "status"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    COMMENT = "comment"
    DELETED = "deleted"

    CHOICES = [CREATED, UPDATED, STATUS, ASSIGNED, UNASSIGNED, COMMENT, DELETED]


class DepartmentBudgetStatus:
    """Where a department stands against its owner-set budget limit,
    based on how much of ``budget_limit`` remains unspent.

    - ``HIGH``     – remaining amount is more than 25% of the limit.
    - ``LOW``      – remaining amount is at or below 25% of the limit (but > 0).
    - ``EMPTY``    – remaining amount is 0 (or the limit has been exceeded).
    - ``NO_LIMIT`` – the owner hasn't set a limit for this department.
    """
    NO_LIMIT = "no_limit"
    HIGH = "high"
    LOW = "low"
    EMPTY = "empty"

    CHOICES = [NO_LIMIT, HIGH, LOW, EMPTY]


def compute_budget_status(limit: Decimal | None, used: Decimal) -> str:
    """Shared status rule for anything measured against a budget limit
    (departments and individual employee limits alike). See
    ``DepartmentBudgetStatus`` for what each value means."""
    if limit is None:
        return DepartmentBudgetStatus.NO_LIMIT
    remaining = limit - used
    if remaining <= 0:
        return DepartmentBudgetStatus.EMPTY
    if remaining <= limit * Decimal("0.25"):
        return DepartmentBudgetStatus.LOW
    return DepartmentBudgetStatus.HIGH


@dataclass(slots=True)
class B2BLeadRequest(HardDeleteBaseModel):
    """A public 'become a partner' application submitted by a prospective
    business owner — not yet a B2BCompany. Reviewed manually by staff, who
    onboard the company via ``create_b2b_owner`` once approved."""
    id: int | None = None
    full_name: str | None = None
    company_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    _meta = SimpleNamespace(db_table="b2b_lead_request")


@dataclass(slots=True)
class B2BCompany(HardDeleteBaseModel):
    id: int | None = None
    name: str | None = None
    legal_name: str | None = None
    inn: str | None = None
    city: str | None = None
    district: str | None = None
    legal_address: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    is_active: bool = True
    _meta = SimpleNamespace(db_table="b2b_company")


@dataclass(slots=True)
class B2BUser(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    phone: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role: str = B2BUserRole.PERFORMER
    is_active: bool = True
    _meta = SimpleNamespace(db_table="b2b_user")


@dataclass(slots=True)
class B2BUserSession(HardDeleteBaseModel):
    id: int | None = None
    user_id: int | None = None
    token: str | None = None
    expires_at: datetime | None = None
    _meta = SimpleNamespace(db_table="b2b_user_session")


@dataclass(slots=True)
class B2BDepartment(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    name: str | None = None
    color: str | None = None
    _meta = SimpleNamespace(db_table="b2b_department")


@dataclass(slots=True)
class B2BEmployee(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    department_id: int | None = None
    full_name: str | None = None
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None
    passport_series: str | None = None
    passport_pinfl: str | None = None
    passport_upload_front: str | None = None
    passport_upload_back: str | None = None
    photo: str | None = None
    individual_limit: Decimal | None = None
    status: str = EmployeeStatus.AVAILABLE
    role: str = EmployeeRole.EMPLOYEE
    is_active: bool = True
    _meta = SimpleNamespace(db_table="b2b_employee")


@dataclass(slots=True)
class BusinessTrip(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    name: str | None = None
    destination_city: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: Decimal | None = None
    status: str = TripStatus.DRAFT
    created_by: int | None = None
    notes: str | None = None
    _meta = SimpleNamespace(db_table="b2b_business_trip")


@dataclass(slots=True)
class TripEmployee(HardDeleteBaseModel):
    id: int | None = None
    trip_id: int | None = None
    employee_id: int | None = None
    property_id: int | None = None
    room_id: int | None = None
    check_in: date | None = None
    check_out: date | None = None
    pms_booking_id: int | None = None
    status: str = TripEmployeeStatus.INVITED
    _meta = SimpleNamespace(db_table="b2b_trip_employee")


@dataclass(slots=True)
class TravelPolicy(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    budget_per_trip: Decimal | None = None
    monthly_budget: Decimal | None = None
    allowed_star_ratings: list = field(default_factory=list)
    allowed_weel_classifications: list = field(default_factory=list)
    blacklisted_properties: list = field(default_factory=list)
    preferred_properties: list = field(default_factory=list)
    _meta = SimpleNamespace(db_table="b2b_travel_policy")


@dataclass(slots=True)
class TravelPolicyRule(HardDeleteBaseModel):
    id: int | None = None
    policy_id: int | None = None
    applies_to: str = "all"
    target_id: int | None = None
    budget_limit: Decimal | None = None
    _meta = SimpleNamespace(db_table="b2b_travel_policy_rule")


@dataclass(slots=True)
class BudgetRequest(HardDeleteBaseModel):
    id: int | None = None
    trip_id: int | None = None
    employee_id: int | None = None
    department_id: int | None = None
    requested_by: int | None = None
    amount: Decimal | None = None
    description: str | None = None
    status: str = BudgetRequestStatus.PENDING
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    review_description: str | None = None
    _meta = SimpleNamespace(db_table="b2b_budget_request")


@dataclass(slots=True)
class TravelVoucher(HardDeleteBaseModel):
    id: int | None = None
    trip_id: int | None = None
    voucher_number: str | None = None
    pdf_url: str | None = None
    generated_at: datetime | None = None
    _meta = SimpleNamespace(db_table="b2b_travel_voucher")
