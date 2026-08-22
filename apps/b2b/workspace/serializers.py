from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.b2b.models import LeadActivityKind, LeadStage, LeadStatus
from apps.b2b.workspace.repository import (
    EVENT_TYPES,
    LEAD_LOST_REASONS,
    LEAD_SOURCES,
    LEAD_STAGES,
    TASK_PRIORITIES,
    TASK_STATUSES,
)

LEAD_ACTIVITY_KINDS = tuple(LeadActivityKind.CHOICES)


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _clean_phone(value: str) -> str:
    value = value.replace(" ", "").strip()
    return value if value.startswith("+") else "+" + value


class WorkspaceLoginSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value: str) -> str:
        return _clean_phone(value)


class WorkspaceLoginVerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    otp = serializers.CharField(min_length=4, max_length=6)

    def validate_phone(self, value: str) -> str:
        return _clean_phone(value)


class WorkspaceRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


# ─── Output ───────────────────────────────────────────────────────────────────

class TeamMemberSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    # The handle, without its "@" — the app adds that where it draws it, the
    # same way it is typed into the search.
    username = serializers.CharField(allow_null=True, required=False)
    position = serializers.CharField(allow_null=True, required=False)
    role = serializers.CharField()
    department_name = serializers.CharField(allow_null=True, required=False)
    department_color = serializers.CharField(allow_null=True, required=False)
    phone = serializers.CharField(allow_null=True, required=False)
    email = serializers.CharField(allow_null=True, required=False)
    photo = serializers.CharField(allow_null=True, required=False)
    status = serializers.CharField(required=False)


class SupportMessageSerializer(serializers.Serializer):
    """One line of an employee's conversation with WEEL support.

    ``is_staff`` is the whole of what the app needs to place a bubble — the
    employee only ever talks to one counterparty here, so which named person
    answered is the admin inbox's business, not the phone's.
    """

    id = serializers.IntegerField()
    text = serializers.CharField()
    is_staff = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class SupportMessageCreateSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=4000, trim_whitespace=True)

    def validate_text(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError(_("Message cannot be empty."))
        return value.strip()


class SupportThreadSerializer(serializers.Serializer):
    """One row of the admin inbox — a person waiting, and how long they have
    been waiting. Derived per employee; there is no thread table."""

    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    phone = serializers.CharField(allow_null=True, required=False)
    photo = serializers.CharField(allow_null=True, required=False)
    company_id = serializers.IntegerField()
    company_name = serializers.CharField(allow_null=True, required=False)
    message_count = serializers.IntegerField()
    unread_count = serializers.IntegerField()
    last_message = serializers.CharField(allow_null=True, required=False)
    last_message_at = serializers.DateTimeField(allow_null=True, required=False)


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    company_id = serializers.IntegerField()
    company_name = serializers.CharField(allow_null=True, required=False)
    full_name = serializers.CharField()
    position = serializers.CharField(allow_null=True, required=False)
    role = serializers.CharField()
    phone = serializers.CharField(allow_null=True, required=False)
    email = serializers.CharField(allow_null=True, required=False)
    photo = serializers.CharField(allow_null=True, required=False)
    department_name = serializers.CharField(allow_null=True, required=False)
    # Tasks finished in the current calendar month. Sent from here because the
    # profile screen prints it under the name and the app holds only the tasks
    # it happens to have fetched, which is not the same set.
    completed_this_month = serializers.IntegerField()
    permissions = serializers.DictField(child=serializers.BooleanField())


class SubtaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    is_done = serializers.BooleanField()


class TaskCommentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    author_id = serializers.IntegerField()
    text = serializers.CharField()
    created_at = serializers.DateTimeField()


class TaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    priority = serializers.CharField()
    project = serializers.CharField(allow_null=True, required=False)
    due_date = serializers.DateTimeField(allow_null=True, required=False)
    author_id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    assignee_ids = serializers.ListField(child=serializers.IntegerField())
    subtasks = SubtaskSerializer(many=True)
    comments = TaskCommentSerializer(many=True)
    can_edit = serializers.BooleanField()
    can_delete = serializers.BooleanField()
    can_change_status = serializers.BooleanField()


class TaskListSerializer(serializers.Serializer):
    results = TaskSerializer(many=True)
    counters = serializers.DictField(child=serializers.IntegerField())


class CalendarEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    event_type = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    all_day = serializers.BooleanField()
    location = serializers.CharField(allow_null=True, required=False)
    notes = serializers.CharField(allow_null=True, required=False)
    author_id = serializers.IntegerField()
    participant_ids = serializers.ListField(child=serializers.IntegerField())
    can_edit = serializers.BooleanField()


class ChatMessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    thread_id = serializers.IntegerField(required=False)
    sender_id = serializers.IntegerField()
    text = serializers.CharField(allow_blank=True)
    reply_to_id = serializers.IntegerField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()

    class Meta:
        # `chat.ChatMessageSerializer` is a different shape (conversation_id,
        # content, read flags). Without distinct ref_names drf_yasg refuses to
        # build the combined schema at all.
        ref_name = "WorkspaceChatMessage"


class LeadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    company_name = serializers.CharField()
    # Null once claimed, for anyone but the claimer and the manager who
    # posted it — see `_lead_payload`.
    contact_full_name = serializers.CharField(allow_null=True)
    contact_phone = serializers.CharField(allow_null=True)
    # Withheld with the two above, for the same reason.
    contact_position = serializers.CharField(allow_null=True, required=False)
    contact_email = serializers.CharField(allow_null=True, required=False)
    contact_address = serializers.CharField(allow_null=True, required=False)
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    #: SUM of the lead's line items, mirrored onto the row — see
    #: `repository.recalc_lead_amount`.
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    status = serializers.CharField()
    #: Where the lead sits inside `status`; the funnel's own step.
    stage = serializers.ChoiceField(choices=LEAD_STAGES)
    source = serializers.ChoiceField(choices=LEAD_SOURCES)
    author_id = serializers.IntegerField()
    claimed_by_id = serializers.IntegerField(allow_null=True, required=False)
    claimed_at = serializers.DateTimeField(allow_null=True, required=False)
    completed_at = serializers.DateTimeField(allow_null=True, required=False)
    #: Set only on a lead closed as `lost`, and cleared by any move off it.
    lost_reason = serializers.ChoiceField(
        choices=LEAD_LOST_REASONS, allow_null=True, required=False
    )
    lost_note = serializers.CharField(allow_null=True, required=False)
    #: The directory card this deal is against. Null on every lead raised
    #: before the directory existed.
    customer_id = serializers.IntegerField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()
    can_claim = serializers.BooleanField()
    can_complete = serializers.BooleanField()
    can_view_details = serializers.BooleanField()
    #: Whether this viewer is the employee running the deal — the one flag
    #: behind every write on the detail screen. False for the rest of the
    #: company, the owner and the managers included: they watch the board.
    can_work = serializers.BooleanField(required=False)
    can_change_stage = serializers.BooleanField(required=False)
    can_assign = serializers.BooleanField(required=False)
    can_delete = serializers.BooleanField(required=False)
    #: Set on every row of the board, so the card can show a total and a task
    #: count without a second request per lead.
    item_count = serializers.IntegerField(required=False)
    task_count = serializers.IntegerField(required=False)


class LeadItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    #: The free-text qualifier the design prints under the name — "3 oy",
    #: "1 marta". Not a unit of measure, so it is not validated as one.
    unit = serializers.CharField(allow_blank=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    position = serializers.IntegerField()


class LeadActivitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=LEAD_ACTIVITY_KINDS)
    #: The employee's note for a `comment`; for a `stage` move the two stage
    #: names as `from>to`; empty otherwise.
    text = serializers.CharField(allow_blank=True)
    author_id = serializers.IntegerField(allow_null=True)
    author_name = serializers.CharField(allow_null=True, required=False)
    author_photo = serializers.CharField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()


class LeadDetailSerializer(LeadSerializer):
    items = LeadItemSerializer(many=True)
    activity = LeadActivitySerializer(many=True)
    tasks = TaskSerializer(many=True)


class LeadListSerializer(serializers.Serializer):
    results = LeadSerializer(many=True)


class WorkspaceFileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    size = serializers.IntegerField()
    author_id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    url = serializers.CharField()


class WorkspaceFileListSerializer(serializers.Serializer):
    results = WorkspaceFileSerializer(many=True)


class WorkspaceFilePatchSerializer(serializers.Serializer):
    """What the drive screen may change about a file: its name, and which
    folder it sits in.

    ``folder_id`` is nullable on purpose — null is "back on the drive itself",
    which is how a file leaves a folder without being deleted.
    """

    name = serializers.CharField(max_length=300, trim_whitespace=True, required=False)
    folder_id = serializers.IntegerField(required=False, allow_null=True)


class WorkspaceFolderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    author_id = serializers.IntegerField()
    file_count = serializers.IntegerField()
    size_bytes = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class WorkspaceFolderListSerializer(serializers.Serializer):
    results = WorkspaceFolderSerializer(many=True)


class WorkspaceFolderWriteSerializer(serializers.Serializer):
    """A folder is a name and nothing else.

    Trimmed and required: a folder called " " is a row nobody can point at on
    the screen, and `allow_blank` would let one through.
    """

    name = serializers.CharField(max_length=120, trim_whitespace=True)


class ChatThreadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    group_name = serializers.CharField(allow_null=True, required=False)
    participant_ids = serializers.ListField(child=serializers.IntegerField())
    unread = serializers.IntegerField()
    is_pinned = serializers.BooleanField()
    is_muted = serializers.BooleanField()
    last_message = ChatMessageSerializer(allow_null=True, required=False)


class CustomerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    phone = serializers.CharField()
    company_name = serializers.CharField(allow_null=True, required=False)
    position = serializers.CharField(allow_null=True, required=False)
    #: How many leads have been raised against this card. The "N ta bitim"
    #: badge the search results show is the whole reason to search first.
    deal_count = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class CustomerListSerializer(serializers.Serializer):
    results = CustomerSerializer(many=True)


class CrmCustomerSerializer(serializers.Serializer):
    """One row of the CRM directory — a card with its deal footprint."""

    id = serializers.IntegerField()
    full_name = serializers.CharField()
    phone = serializers.CharField()
    company_name = serializers.CharField(allow_null=True, required=False)
    position = serializers.CharField(allow_null=True, required=False)
    deal_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    #: The newest of created/claimed/completed across the customer's leads —
    #: the list screen's "Oxirgi aloqa".
    last_activity_at = serializers.DateTimeField(allow_null=True)
    #: True while at least one of the customer's leads is still open.
    is_active = serializers.BooleanField()


class CrmCustomerListSerializer(serializers.Serializer):
    results = CrmCustomerSerializer(many=True)


class CrmDealSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    stage = serializers.ChoiceField(choices=LeadStage.CHOICES)
    status = serializers.ChoiceField(choices=LeadStatus.CHOICES)
    created_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)


class CrmMonthlyAmountSerializer(serializers.Serializer):
    month = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class CrmCustomerDetailSerializer(CrmCustomerSerializer):
    email = serializers.CharField(allow_null=True, required=False)
    address = serializers.CharField(allow_null=True, required=False)
    #: Whoever has claimed the most of this customer's leads.
    top_manager_name = serializers.CharField(allow_null=True)
    monthly_amounts = CrmMonthlyAmountSerializer(many=True)
    deals = CrmDealSerializer(many=True)


# ─── Input ────────────────────────────────────────────────────────────────────

class TaskWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    description = serializers.CharField(allow_blank=True, required=False, default="")
    status = serializers.ChoiceField(choices=TASK_STATUSES, required=False, default="todo")
    priority = serializers.ChoiceField(choices=TASK_PRIORITIES, required=False, default="medium")
    project = serializers.CharField(max_length=200, required=False, allow_null=True, allow_blank=True)
    due_date = serializers.DateTimeField(required=False, allow_null=True)
    assignee_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    subtasks = serializers.ListField(
        child=serializers.CharField(max_length=300), required=False, default=list
    )


class LeadItemWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=300)
    unit = serializers.CharField(max_length=100, required=False, allow_blank=True)
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False
    )


class LeadWriteSerializer(serializers.Serializer):
    """What the "Yangi lead" sheet sends.

    Only the contact is genuinely required. The sheet asks for a customer, a
    sum and — optionally — the lines the sum is made of, and everything else the
    board needs is derived in `validate`: a lead nobody can phone is useless,
    and a lead whose product name has not been decided yet is perfectly normal.
    """

    #: Set when the customer was picked out of the directory rather than typed.
    #: The server then trusts the card over the fields, which is why the sheet
    #: shows them locked.
    customer_id = serializers.IntegerField(required=False, allow_null=True)
    company_name = serializers.CharField(
        max_length=300, required=False, allow_blank=True
    )
    contact_full_name = serializers.CharField(max_length=300)
    contact_phone = serializers.CharField(max_length=20)
    product_name = serializers.CharField(
        max_length=300, required=False, allow_blank=True
    )
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False
    )
    #: The deal's value as typed. Ignored when `items` are sent — their sum is
    #: the total then, and two numbers that can disagree is one too many.
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False
    )
    #: The first thing said about the deal. Stored as a comment in the lead's
    #: history, not as a column.
    note = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, allow_null=True
    )
    #: "Mas'ul menejer: Siz" — the creator takes the lead rather than posting it
    #: to the board for anyone to claim.
    assign_to_me = serializers.BooleanField(required=False, default=True)
    contact_position = serializers.CharField(
        max_length=200, required=False, allow_blank=True, allow_null=True
    )
    contact_email = serializers.EmailField(
        max_length=254, required=False, allow_blank=True, allow_null=True
    )
    contact_address = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    source = serializers.ChoiceField(choices=LEAD_SOURCES, required=False)
    #: The priced lines. Sent whole; the server totals them onto the lead.
    items = LeadItemWriteSerializer(many=True, required=False)

    def validate_contact_phone(self, value: str) -> str:
        return _clean_phone(value)

    def validate(self, attrs: dict) -> dict:
        # The board prints a company on every card and the funnel prints a
        # product; neither is worth blocking a lead over, so they fall back to
        # what the sheet does know rather than being demanded from it.
        items = attrs.get("items") or []
        if not (attrs.get("company_name") or "").strip():
            attrs["company_name"] = attrs["contact_full_name"]
        if not (attrs.get("product_name") or "").strip():
            attrs["product_name"] = (
                items[0]["name"] if items else _("Deal")
            )
        if attrs.get("quantity") is None:
            attrs["quantity"] = 1
        return attrs


class LeadStageWriteSerializer(serializers.Serializer):
    """A move along the funnel, and what closing it as lost has to say for
    itself."""

    stage = serializers.ChoiceField(choices=LEAD_STAGES)
    lost_reason = serializers.ChoiceField(
        choices=LEAD_LOST_REASONS, required=False, allow_null=True
    )
    #: Free text beside the reason, and the note on any other move.
    note = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, allow_null=True
    )

    def validate(self, attrs: dict) -> dict:
        # A lost deal with no reason is a number nobody can act on, so this is
        # the one stage the client cannot post bare.
        if attrs.get("stage") == LeadStage.LOST and not attrs.get("lost_reason"):
            raise serializers.ValidationError(
                {"lost_reason": _("Choose why the deal was lost.")}
            )
        return attrs


class LeadAssignWriteSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()


class LeadCommentWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000)


class TaskPatchSerializer(TaskWriteSerializer):
    """Same fields, all optional — PATCH only touches what it was sent."""

    title = serializers.CharField(max_length=300, required=False)
    status = serializers.ChoiceField(choices=TASK_STATUSES, required=False)
    priority = serializers.ChoiceField(choices=TASK_PRIORITIES, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    assignee_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    subtasks = serializers.ListField(child=serializers.CharField(max_length=300), required=False)


class TaskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TASK_STATUSES)


class TaskCommentWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000)


class EventWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    event_type = serializers.ChoiceField(choices=EVENT_TYPES, required=False, default="meeting")
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    all_day = serializers.BooleanField(required=False, default=False)
    location = serializers.CharField(max_length=300, required=False, allow_null=True, allow_blank=True)
    notes = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    participant_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)

    def validate(self, attrs):
        starts_at = attrs.get("starts_at")
        ends_at = attrs.get("ends_at")
        if starts_at and ends_at and ends_at < starts_at:
            raise serializers.ValidationError({"ends_at": "Event cannot end before it starts."})
        return attrs


class EventPatchSerializer(EventWriteSerializer):
    title = serializers.CharField(max_length=300, required=False)
    starts_at = serializers.DateTimeField(required=False)
    ends_at = serializers.DateTimeField(required=False)
    participant_ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class ThreadCreateSerializer(serializers.Serializer):
    member_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    group_name = serializers.CharField(max_length=200, required=False, allow_null=True, allow_blank=True)

    def validate(self, attrs):
        members = [m for m in dict.fromkeys(attrs["member_ids"])]
        attrs["member_ids"] = members
        if not attrs.get("group_name") and len(members) != 1:
            raise serializers.ValidationError(
                {"group_name": "A chat with more than one other person needs a group name."}
            )
        return attrs


class MessageWriteSerializer(serializers.Serializer):
    """A chat message.

    ``text`` is optional only when the request carries an attachment — the view
    sets ``allow_empty_text`` in the context for that. A photo with no caption
    is a message; an empty envelope is not.
    """

    text = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")
    reply_to_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_text(self, value: str) -> str:
        value = value.strip()
        if not value and not self.context.get("allow_empty_text"):
            raise serializers.ValidationError("Message cannot be empty.")
        return value


class StorageKindUsageSerializer(serializers.Serializer):
    bytes = serializers.IntegerField()
    files = serializers.IntegerField()


class StorageUsageSerializer(serializers.Serializer):
    """``GET /storage/`` — the company's 5 GB allowance and what is in it."""

    used_bytes = serializers.IntegerField()
    quota_bytes = serializers.IntegerField()
    available_bytes = serializers.IntegerField()
    used_percent = serializers.FloatField()
    max_upload_bytes = serializers.IntegerField()
    by_kind = serializers.DictField(child=StorageKindUsageSerializer())


class ThreadFlagsSerializer(serializers.Serializer):
    is_pinned = serializers.BooleanField(required=False)
    is_muted = serializers.BooleanField(required=False)


# ─── Employee of the month ──────────────────────────────────────────────────

class EmployeeMonthlyStatSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    photo = serializers.CharField(allow_null=True, required=False)
    position = serializers.CharField(allow_null=True, required=False)
    department_name = serializers.CharField(allow_null=True, required=False)
    # Deals this person closed as won in the same month — what the owner reads
    # beside the task count when picking the month's best.
    deals_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()
    due_count = serializers.IntegerField()
    on_time_count = serializers.IntegerField()
    on_time_rate = serializers.SerializerMethodField()
    # Days this month with an attendance row. Showing up is part of the month
    # the owner is judging, so it is reported here beside the task and deal
    # counts rather than living only on the daily roll call, which is thrown
    # away the moment the day turns over.
    present_days = serializers.IntegerField()
    absent_days = serializers.IntegerField()
    # Absences with no reason written against them — "sababsiz". Its own
    # number because a fortnight off sick and a fortnight of silence are not
    # the same month, and one total cannot tell them apart.
    unexcused_days = serializers.IntegerField()
    attendance_rate = serializers.SerializerMethodField()

    def get_on_time_rate(self, obj) -> float | None:
        # None rather than 0 when nobody had a deadline this month — a 0% rate
        # would read as "always late", which nothing in the data supports.
        due = obj["due_count"]
        return round(obj["on_time_count"] / due, 4) if due else None

    def get_attendance_rate(self, obj) -> float | None:
        # Out of the days somebody actually accounted for, not out of the
        # calendar: a company that only marks attendance on three days a week
        # would otherwise read as everybody being absent half the month.
        marked = obj["present_days"] + obj["absent_days"]
        return round(obj["present_days"] / marked, 4) if marked else None


class EmployeeOfMonthSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    photo = serializers.CharField(allow_null=True, required=False)
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    selected_at = serializers.DateTimeField()


class EmployeeOfMonthSelectSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()


# ─── Attendance ─────────────────────────────────────────────────────────────

ATTENDANCE_STATUSES = ("present", "absent", "late", "remote")


class AttendanceEntrySerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    position = serializers.CharField(allow_null=True, required=False)
    department_name = serializers.CharField(allow_null=True, required=False)
    # Null for somebody nobody has accounted for yet today — which is a third
    # state, not the same as being marked absent.
    status = serializers.CharField(allow_null=True)
    checked_in_at = serializers.DateTimeField(allow_null=True, required=False)
    reason = serializers.CharField(allow_null=True, required=False)
    marked_by_id = serializers.IntegerField(allow_null=True, required=False)


class AttendanceDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    unmarked = serializers.IntegerField()
    my_status = serializers.CharField(allow_null=True)
    # What the caller wrote when they reported themselves absent, so the app
    # can show it back instead of only knowing that they did.
    my_reason = serializers.CharField(allow_null=True, required=False)
    entries = AttendanceEntrySerializer(many=True)


class AttendanceMarkSerializer(serializers.Serializer):
    """A manager recording someone's day."""

    status = serializers.ChoiceField(choices=ATTENDANCE_STATUSES)
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    date = serializers.DateField(required=False)


class AttendanceCheckInSerializer(serializers.Serializer):
    """An employee marking themselves in. The time is the server's, not the
    phone's, or a wrong device clock becomes an arrival time nobody can argue
    with. Coordinates are only required when the company has geofencing on —
    the view is what knows that, not this serializer, since the same "I'm
    here" tap has to work for both."""

    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)


class AttendanceSelfAbsenceSerializer(serializers.Serializer):
    """An employee reporting their own absence.

    The reason is required, unlike on a manager's mark: this is the only way
    into the day for somebody the geofence turned away, and an absence they
    filed themselves with nothing written against it is indistinguishable from
    never opening the app at all.
    """

    reason = serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True)
    date = serializers.DateField(required=False)


class AttendanceLocationSerializer(serializers.Serializer):
    """``GET /attendance/location/`` — the office point a check-in is
    measured against, and whether that measuring is turned on."""

    is_enabled = serializers.BooleanField()
    latitude = serializers.FloatField(allow_null=True)
    longitude = serializers.FloatField(allow_null=True)
    radius_meters = serializers.IntegerField()
    updated_at = serializers.DateTimeField(allow_null=True, required=False)


class AttendanceLocationUpdateSerializer(serializers.Serializer):
    """The owner's write to the geofence.

    Coordinates are optional here on purpose: switching it back on without
    resending the point should reuse the one already on file. Whether that
    leaves the geofence with no point at all — enabling it for the first
    time without ever having sent one — is for the view to reject, since only
    it knows what is already stored.
    """

    is_enabled = serializers.BooleanField()
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    radius_meters = serializers.IntegerField(required=False, min_value=10, max_value=5000)
