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
    one place only (``repository.set_lead_stage``): reaching ``WON``, ``LOST``
    or ``ARCHIVED`` completes the lead, and nothing else changes the status.
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
    #: Closed without a verdict — a duplicate, a wrong number, a lead that
    #: went quiet and is not worth carrying as an open deal any longer. Not a
    #: third outcome alongside won and lost: it asks for no reason, and does
    #: not count toward the conversion rate or either won/lost total, because
    #: nothing about it was decided. It only takes the deal off the board.
    #: Added after the funnel screen shipped, for the reason ``CONTRACT`` is
    #: at the end rather than in ``ORDER``'s place — see the note there.
    ARCHIVED = "archived"

    CHOICES = [
        NEW, INTERESTED, PROPOSAL, NEGOTIATION, CONTRACT, WON, LOST, ARCHIVED,
    ]

    #: The funnel's own order, which ``CHOICES`` cannot carry — a stage added
    #: later has to go on the end of the choices for the existing rows' sake.
    #: This is what "which stages come after this one" is read from.
    ORDER = [
        NEW, INTERESTED, PROPOSAL, NEGOTIATION, CONTRACT, WON, LOST, ARCHIVED,
    ]

    #: The stages that close a lead — reaching any of these sets the status to
    #: ``LeadStatus.COMPLETED``.
    CLOSED = [WON, LOST, ARCHIVED]

    #: The two endings the sales report scores — the deals that were actually
    #: *decided*. ``ARCHIVED`` is deliberately outside both this and
    #: ``LOST``'s reason requirement: reports read a low win rate as a
    #: pipeline problem, and a pile of dead numbers and duplicates archived
    #: out of the funnel is not one.
    DECIDED = [WON, LOST]


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


class LeadQuality:
    """Whether the enquiry was worth working at all.

    Two answers and no third, because the question is not how promising a deal
    looks — the funnel's own stages already say that, and a deal that is
    progressing is a deal somebody rates. This asks the one thing the stages
    cannot: was this a real customer, or noise. A wrong number, a competitor
    fishing for prices, a form filled in by a bot — none of those ever reach a
    stage that would record what they were.

    Unmarked is the third state and it is the absence of a value, not a member
    here: most leads are never judged either way, and a default would put an
    opinion on every row nobody has looked at yet.
    """

    GOOD = "good"
    BAD = "bad"

    CHOICES = [GOOD, BAD]


class LeadSource:
    """Where a lead came from. Reported on every card, so a company can see
    which channel is actually filling the funnel."""
    WEBSITE = "website"
    CALL = "call"
    REFERRAL = "referral"
    EXHIBITION = "exhibition"
    MANUAL = "manual"
    #: A Facebook/Instagram lead-ad form, delivered by Meta's `leadgen`
    #: webhook — see `apps/b2b/integrations`. Unlike the five above, nobody
    #: may choose it by hand: it is written only by the ingest path, so a
    #: card marked "Meta" is one the integration actually brought in.
    META = "meta"

    CHOICES = [WEBSITE, CALL, REFERRAL, EXHIBITION, MANUAL, META]

    #: What a person may pick when raising a lead themselves. `META` is
    #: deliberately absent — see above.
    MANUAL_CHOICES = [WEBSITE, CALL, REFERRAL, EXHIBITION, MANUAL]


class LeadKind:
    """Whether a row in ``b2b_workspace_lead`` is a deal being worked or one
    already done.

    ``LEAD`` is the funnel's own row: raised, claimed, dragged through the
    stages. ``QUICK_SALE`` is the sale a salesperson records after the fact —
    somebody walked in, paid, left. It lives in the same table because it is
    the same thing to every reader downstream (the CRM card's deal history,
    the customer's total, the sales figures), and only the funnel screens care
    about the difference: ``list_leads`` hides quick sales from the board
    unless asked for them by name.

    A quick sale is therefore born finished — ``LeadStatus.COMPLETED`` /
    ``LeadStage.WON``, claimed by its author — which is what makes it count in
    every total without ever appearing as something to work.
    """

    LEAD = "lead"
    QUICK_SALE = "quick_sale"

    CHOICES = [LEAD, QUICK_SALE]


class PaymentMethod:
    """How a quick sale was paid for.

    Only quick sales carry one: a lead is an intention and has nothing to pay
    with yet, while a sale that has already happened was settled somehow, and
    that is the one thing the funnel never had to record. A fixed list rather
    than free text for the reason ``LeadLostReason`` gives — five answers that
    can be counted beat a thousand sentences that cannot.
    """

    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"
    INSTALLMENT = "installment"
    OTHER = "other"

    CHOICES = [CASH, CARD, TRANSFER, INSTALLMENT, OTHER]


class IntegrationProvider:
    """The outside services a workspace can plug into its funnel.

    One name so far. It is a column rather than a boolean because the second
    one (a Telegram bot, a website form) lands in the same table, and a
    `has_meta` flag would have to become this on the day it does.
    """

    META = "meta"

    CHOICES = [META]

    LABELS = {META: "Meta (Facebook / Instagram)"}


class IntegrationStatus:
    """Whether the connection is actually working.

    ``ERROR`` is not the same as ``DISCONNECTED``: the first is a token Meta
    stopped accepting — the rows are still here and reconnecting fixes it —
    and the second is somebody having deliberately unplugged it.
    """

    CONNECTED = "connected"
    ERROR = "error"
    DISCONNECTED = "disconnected"

    CHOICES = [CONNECTED, ERROR, DISCONNECTED]


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
    #: The deadline was set, moved or cleared. ``text`` is the new date as
    #: ISO-8601, or empty where it was cleared.
    DUE_DATE = "due_date"
    #: The lead was marked good or bad, or the mark was taken off. ``text`` is
    #: the new `LeadQuality`, or empty where it was cleared.
    QUALITY = "quality"

    CHOICES = [
        CREATED, CLAIMED, ASSIGNED, STAGE, COMMENT, COMPLETED, DUE_DATE, QUALITY,
    ]


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
