from __future__ import annotations

from rest_framework import serializers

from apps.b2b.models import LeadActivityKind
from apps.b2b.workspace.repository import (
    EVENT_TYPES,
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
    position = serializers.CharField(allow_null=True, required=False)
    role = serializers.CharField()
    department_name = serializers.CharField(allow_null=True, required=False)
    department_color = serializers.CharField(allow_null=True, required=False)
    phone = serializers.CharField(allow_null=True, required=False)
    email = serializers.CharField(allow_null=True, required=False)
    photo = serializers.CharField(allow_null=True, required=False)
    status = serializers.CharField(required=False)


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
    created_at = serializers.DateTimeField()
    can_claim = serializers.BooleanField()
    can_complete = serializers.BooleanField()
    can_view_details = serializers.BooleanField()
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


class ChatThreadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    group_name = serializers.CharField(allow_null=True, required=False)
    participant_ids = serializers.ListField(child=serializers.IntegerField())
    unread = serializers.IntegerField()
    is_pinned = serializers.BooleanField()
    is_muted = serializers.BooleanField()
    last_message = ChatMessageSerializer(allow_null=True, required=False)


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
    company_name = serializers.CharField(max_length=300)
    contact_full_name = serializers.CharField(max_length=300)
    contact_phone = serializers.CharField(max_length=20)
    product_name = serializers.CharField(max_length=300)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
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


class LeadStageWriteSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=LEAD_STAGES)


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
    completed_count = serializers.IntegerField()
    due_count = serializers.IntegerField()
    on_time_count = serializers.IntegerField()
    on_time_rate = serializers.SerializerMethodField()

    def get_on_time_rate(self, obj) -> float | None:
        # None rather than 0 when nobody had a deadline this month — a 0% rate
        # would read as "always late", which nothing in the data supports.
        due = obj["due_count"]
        return round(obj["on_time_count"] / due, 4) if due else None


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
