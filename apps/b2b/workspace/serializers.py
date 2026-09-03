from __future__ import annotations

import re

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.b2b.models import LeadActivityKind, LeadKind, LeadStage, LeadStatus
from apps.b2b.workspace.repository import (
    EVENT_TYPES,
    LEAD_KINDS,
    LEAD_LOST_REASONS,
    LEAD_MANUAL_SOURCES,
    LEAD_QUALITIES,
    LEAD_SOURCES,
    LEAD_STAGES,
    NOTE_COLORS,
    NOTE_KINDS,
    PAYMENT_METHODS,
    TASK_PRIORITIES,
    TASK_STATUSES,
)
from apps.b2b.workspace.secondment import Module, RequestRole

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
    # Somebody lent to this workspace by another one. The app marks the row so
    # it is clear who is here for a while and who was hired here.
    is_guest = serializers.BooleanField(required=False, default=False)
    # Whether they are holding a socket open right now, and when they last
    # were. Not the same thing as `status`, which is available/on_trip/blocked
    # and is a manager's account of where somebody is for days at a time —
    # see `presence.py` for why this one never touches the database.
    is_online = serializers.BooleanField(required=False, default=False)
    last_seen_at = serializers.CharField(allow_null=True, required=False)


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
    username = serializers.CharField(allow_null=True, required=False)
    company_id = serializers.IntegerField()
    company_name = serializers.CharField(allow_null=True, required=False)
    org_id = serializers.IntegerField(allow_null=True, required=False)
    org_name = serializers.CharField(allow_null=True, required=False)
    org_join_code = serializers.CharField(allow_null=True, required=False)
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
    # Set when this identity is somebody lent to the workspace by another one.
    is_guest = serializers.BooleanField(required=False, default=False)
    modules = serializers.ListField(
        child=serializers.CharField(), allow_null=True, required=False
    )
    guest_until = serializers.DateTimeField(allow_null=True, required=False)


class SubtaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    is_done = serializers.BooleanField()


class TaskCommentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    author_id = serializers.IntegerField()
    text = serializers.CharField()
    created_at = serializers.DateTimeField()


class TaskFileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    size = serializers.IntegerField()
    content_type = serializers.CharField(allow_null=True, required=False)
    url = serializers.CharField()


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
    files = TaskFileSerializer(many=True, required=False)
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


class NoteVoiceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    size = serializers.IntegerField()
    content_type = serializers.CharField(allow_null=True, required=False)
    duration_ms = serializers.IntegerField(allow_null=True, required=False)
    url = serializers.CharField()


class NoteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=list(NOTE_KINDS))
    title = serializers.CharField(allow_blank=True)
    body = serializers.CharField(allow_blank=True)
    color = serializers.ChoiceField(choices=list(NOTE_COLORS))
    is_pinned = serializers.BooleanField()
    is_shared = serializers.BooleanField()
    author_id = serializers.IntegerField()
    #: Present on a voice note, null on a typed one — the recording lives in
    #: the shared file table, so what the app gets is a playable URL and never
    #: the storage path behind it.
    voice = NoteVoiceSerializer(allow_null=True, required=False)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    can_edit = serializers.BooleanField()


class NoteWriteSerializer(serializers.Serializer):
    # Both blank-able: a voice note is saved before its recording is uploaded
    # and has neither, and the app names it afterwards.
    title = serializers.CharField(required=False, allow_blank=True, default="")
    body = serializers.CharField(required=False, allow_blank=True, default="")
    kind = serializers.ChoiceField(choices=list(NOTE_KINDS), required=False, default="text")
    color = serializers.ChoiceField(choices=list(NOTE_COLORS), required=False, default="green")
    is_shared = serializers.BooleanField(required=False, default=False)


class NotePatchSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    body = serializers.CharField(required=False, allow_blank=True)
    color = serializers.ChoiceField(choices=list(NOTE_COLORS), required=False)
    is_pinned = serializers.BooleanField(required=False)
    is_shared = serializers.BooleanField(required=False)


class ChatMessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    thread_id = serializers.IntegerField(required=False)
    sender_id = serializers.IntegerField()
    text = serializers.CharField(allow_blank=True)
    reply_to_id = serializers.IntegerField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()
    # Set once a message has been rewritten. The bubble says so; a message
    # whose text changes with nothing to show for it is worse to have in a
    # room than one that cannot be changed at all.
    edited_at = serializers.DateTimeField(allow_null=True, required=False)
    # Who wrote the original, when this is a forward. The person, not the
    # message: the label has to keep saying "Sardordan" after the original
    # room is gone.
    forwarded_from_id = serializers.IntegerField(allow_null=True, required=False)
    # And their name, filled in by the view. Sent with the message rather than
    # left to the client's roster: the roster is only the *current* one, so a
    # forward from somebody who has since left the workspace, been hidden, or
    # was lent by another one had nothing to draw a name from and the label
    # said "Yuborilgan xabar" and named nobody.
    forwarded_from_name = serializers.CharField(allow_null=True, required=False)
    pinned_at = serializers.DateTimeField(allow_null=True, required=False)

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
    #: A deal being worked, or a sale already made — see `LeadKind`. The board
    #: only ever asks for the first; the second is here because the CRM and the
    #: detail card show both and have to be able to tell them apart.
    kind = serializers.ChoiceField(choices=LEAD_KINDS, required=False)
    #: How a quick sale was paid for. Null on every ordinary lead.
    payment_method = serializers.ChoiceField(
        choices=PAYMENT_METHODS, allow_null=True, required=False
    )
    author_id = serializers.IntegerField()
    claimed_by_id = serializers.IntegerField(allow_null=True, required=False)
    claimed_at = serializers.DateTimeField(allow_null=True, required=False)
    completed_at = serializers.DateTimeField(allow_null=True, required=False)
    #: When the deal is meant to be closed by. Null on most leads — a deadline
    #: is something a salesperson sets on a deal they have decided to chase.
    due_date = serializers.DateTimeField(allow_null=True, required=False)
    #: Set only on a lead closed as `lost`, and cleared by any move off it.
    lost_reason = serializers.ChoiceField(
        choices=LEAD_LOST_REASONS, allow_null=True, required=False
    )
    lost_note = serializers.CharField(allow_null=True, required=False)
    #: Whether the enquiry was worth working — see `LeadQuality`. Null on a
    #: lead nobody has judged, which is most of them.
    quality = serializers.ChoiceField(
        choices=LEAD_QUALITIES, allow_null=True, required=False
    )
    #: The directory card this deal is against. Null on every lead raised
    #: before the directory existed.
    customer_id = serializers.IntegerField(allow_null=True, required=False)
    #: Set only on a lead a connected service brought in — see
    #: `apps/b2b/integrations`. `external_id` is the other side's own id for
    #: it, `external_form_name` the form the customer answered, and
    #: `external_data` everything they typed that has no column of its own.
    #: Withheld from everyone but the claimant and a manager, exactly like the
    #: contact fields.
    external_id = serializers.CharField(allow_null=True, required=False)
    external_form_name = serializers.CharField(allow_null=True, required=False)
    external_data = serializers.JSONField(allow_null=True, required=False)
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


class EmployeeStatsSerializer(serializers.Serializer):
    """What one colleague is carrying, for the card their profile shows."""

    employee_id = serializers.IntegerField()
    tasks_done = serializers.IntegerField()
    tasks_in_progress = serializers.IntegerField()
    tasks_todo = serializers.IntegerField()
    tasks_overdue = serializers.IntegerField()


class LeadActivityAttachmentSerializer(serializers.Serializer):
    """The document filed with a history row — today only a stage move carries
    one."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    size = serializers.IntegerField()
    content_type = serializers.CharField(allow_null=True, required=False)
    url = serializers.CharField()


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
    #: Null on every row that has no document filed against it, which is most.
    attachment = LeadActivityAttachmentSerializer(allow_null=True, required=False)


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
    #: The group's picture, already resolved to a URL. Null for a direct chat,
    #: which is drawn with the other person's own avatar.
    photo = serializers.CharField(allow_null=True, required=False)
    participant_ids = serializers.ListField(child=serializers.IntegerField())
    unread = serializers.IntegerField()
    is_pinned = serializers.BooleanField()
    is_muted = serializers.BooleanField()
    last_message = ChatMessageSerializer(allow_null=True, required=False)


class ChatGroupMemberSerializer(TeamMemberSerializer):
    """A roster row plus what this person is *in this room*.

    Inherits the team shape on purpose: the group screen draws the same face,
    name and position the team screen does, and a second, slightly different
    employee payload is how the two drift apart.
    """

    #: "admin" or "member" — see `b2b_chat_member.role`.
    member_role = serializers.CharField()


class ChatGroupSerializer(serializers.Serializer):
    """Everything the group's own screen shows in one response."""

    id = serializers.IntegerField()
    group_name = serializers.CharField(allow_null=True)
    photo = serializers.CharField(allow_null=True, required=False)
    created_by = serializers.IntegerField(allow_null=True, required=False)
    created_at = serializers.CharField(allow_null=True, required=False)
    member_count = serializers.IntegerField()
    #: The caller's own standing, so the app can draw the screen before it has
    #: found itself in `members`.
    my_role = serializers.CharField()
    #: Whether this caller may rename the room, change its picture and move
    #: people in and out. Computed by the same rule the write endpoints
    #: enforce, so a button is never offered that the server will refuse.
    can_manage = serializers.BooleanField()
    members = ChatGroupMemberSerializer(many=True)


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
    #: A deal worked through the funnel, or a sale recorded after the fact.
    #: Both are deals to this list — it is the customer's history and a sale
    #: belongs in it — but the row says which, and prints the matching
    #: reference.
    kind = serializers.ChoiceField(choices=LEAD_KINDS, required=False)
    payment_method = serializers.ChoiceField(
        choices=PAYMENT_METHODS, allow_null=True, required=False
    )
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
    #: Unbounded on purpose. The mobile sheet has no separate title box — it
    #: titles a task by the first line of the description — so a long first
    #: paragraph is a normal title, and a cap here threw the whole draft away.
    #: The column behind it is TEXT.
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True, required=False, default="")
    status = serializers.ChoiceField(choices=TASK_STATUSES, required=False, default="todo")
    priority = serializers.ChoiceField(choices=TASK_PRIORITIES, required=False, default="medium")
    project = serializers.CharField(max_length=200, required=False, allow_null=True, allow_blank=True)
    due_date = serializers.DateTimeField(required=False, allow_null=True)
    assignee_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    subtasks = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
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
    #: `LEAD_MANUAL_SOURCES`, not `LEAD_SOURCES`: `meta` is written only by
    #: the ingest path, so a card badged "Meta’dan" is one the integration
    #: actually brought in and not one somebody typed and labelled that way.
    source = serializers.ChoiceField(choices=LEAD_MANUAL_SOURCES, required=False)
    #: The priced lines. Sent whole; the server totals them onto the lead.
    items = LeadItemWriteSerializer(many=True, required=False)
    #: The deadline, where the sheet was given one. Optional, and stays
    #: optional afterwards — see `LeadDueDateWriteSerializer`.
    due_date = serializers.DateTimeField(required=False, allow_null=True)
    #: Which of the two things the "+" menu offers this is. Defaults to the
    #: funnel's own row, so every caller written before the quick sale existed
    #: keeps raising leads.
    kind = serializers.ChoiceField(
        choices=LEAD_KINDS, required=False, default=LeadKind.LEAD
    )
    #: Required on a quick sale and refused on a lead — see `validate`.
    payment_method = serializers.ChoiceField(
        choices=PAYMENT_METHODS, required=False, allow_null=True
    )

    def validate_contact_phone(self, value: str) -> str:
        return _clean_phone(value)

    def validate(self, attrs: dict) -> dict:
        # A sale that has already happened was settled somehow, and the one
        # figure a sales report cannot reconstruct afterwards is how. So the
        # quick sale sheet has to say — and a lead, which has nothing to pay
        # with yet, is not allowed to pretend it does.
        if attrs.get("kind") == LeadKind.QUICK_SALE:
            if not attrs.get("payment_method"):
                raise serializers.ValidationError(
                    {"payment_method": _("Choose how the sale was paid for.")}
                )
        else:
            attrs.pop("payment_method", None)
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


class LeadDueDateWriteSerializer(serializers.Serializer):
    """The deal's deadline, set or cleared.

    ``allow_null`` and no default: sending ``null`` clears the date, and that
    is a different act from not sending the field at all, which this endpoint
    rejects. A required-but-nullable field is what makes "no deadline" sayable
    without making it the accidental outcome of a malformed request.
    """

    due_date = serializers.DateTimeField(allow_null=True)


class LeadQualityWriteSerializer(serializers.Serializer):
    """The mark saying whether the enquiry was worth working.

    ``allow_null`` and no default, for the reason [LeadDueDateWriteSerializer]
    gives: ``null`` takes the mark off, and that has to be sayable without
    being what a malformed request accidentally does.
    """

    quality = serializers.ChoiceField(choices=LEAD_QUALITIES, allow_null=True)


class LeadAssignWriteSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()


class LeadCommentWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000)


class TaskPatchSerializer(TaskWriteSerializer):
    """Same fields, all optional — PATCH only touches what it was sent."""

    title = serializers.CharField(required=False)
    status = serializers.ChoiceField(choices=TASK_STATUSES, required=False)
    priority = serializers.ChoiceField(choices=TASK_PRIORITIES, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    assignee_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    subtasks = serializers.ListField(child=serializers.CharField(), required=False)


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


class ThreadUpdateSerializer(serializers.Serializer):
    """Renaming a group. The picture rides in as a file and is handled by the
    view, the same way a chat attachment is."""

    group_name = serializers.CharField(max_length=200, required=False)

    def validate_group_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A group needs a name.")
        return value


class ThreadMembersSerializer(serializers.Serializer):
    member_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)

    def validate_member_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class ThreadMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["admin", "member"])


class MessageWriteSerializer(serializers.Serializer):
    """A chat message.

    ``text`` is optional only when the request carries an attachment — the view
    sets ``allow_empty_text`` in the context for that. A photo with no caption
    is a message; an empty envelope is not.
    """

    text = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")
    reply_to_id = serializers.IntegerField(required=False, allow_null=True)
    #: The message being forwarded. The server copies its text rather than
    #: trusting the client's — a forward that could say anything and attribute
    #: it to somebody else is not a forward.
    forward_message_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_text(self, value: str) -> str:
        value = value.strip()
        if not value and not self.context.get("allow_empty_text"):
            raise serializers.ValidationError("Message cannot be empty.")
        return value

    def validate(self, attrs):
        # A forward brings its own text with it, so the usual "say something"
        # rule does not apply to one.
        if attrs.get("forward_message_id"):
            attrs["text"] = attrs.get("text") or ""
        return attrs


class MessageEditSerializer(serializers.Serializer):
    """The new text of a message that already exists."""

    text = serializers.CharField(max_length=4000)

    def validate_text(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Message cannot be empty.")
        return value


class MessageReactionSerializer(serializers.Serializer):
    """One reaction, on its way on or off a message."""

    #: Short on purpose. An emoji is one or two code points plus a possible
    #: variation selector; anything longer is somebody sending a sentence
    #: through the reaction field.
    emoji = serializers.CharField(max_length=16)

    def validate_emoji(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Pick a reaction.")
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
    """One person the owner named this month."""

    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    photo = serializers.CharField(allow_null=True, required=False)
    #: Printed under the name on the card, which is why the query carries them
    #: rather than the app fetching the roster to label two tiles.
    position = serializers.CharField(allow_null=True, required=False)
    department_name = serializers.CharField(allow_null=True, required=False)
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    selected_at = serializers.DateTimeField()


class EmployeeOfMonthListSerializer(serializers.Serializer):
    """The month's award: everyone on it, and the first of them repeated flat.

    ``results`` is the answer. The flat fields beside it are the old
    single-winner response, kept because two shipped clients read them —
    `dashboard_weel_uz` and the first B2B mobile app — and neither knows the
    award can name more than one person. They show the first pick instead of
    breaking, which is the right way for them to age out.
    """

    results = EmployeeOfMonthSerializer(many=True)
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    photo = serializers.CharField(allow_null=True, required=False)
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    selected_at = serializers.DateTimeField()


class EmployeeOfMonthSelectSerializer(serializers.Serializer):
    """Who the owner is naming — the whole list, replacing whatever is on file.

    Both keys are accepted and ``validate`` folds them into one: ``employee_ids``
    is what the current app sends, ``employee_id`` is the single-winner body the
    older clients still post. An empty ``employee_ids`` clears the month, which
    is the only way to take back a badge given by mistake — so it is a valid
    request, and sending neither key is not.
    """

    employee_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
    employee_id = serializers.IntegerField(required=False)

    def validate(self, attrs: dict) -> dict:
        if attrs.get("employee_ids") is None:
            if "employee_id" not in attrs:
                raise serializers.ValidationError(
                    {"employee_ids": _("Choose who the badge goes to.")}
                )
            attrs["employee_ids"] = [attrs["employee_id"]]
        # De-duplicated here rather than in the repository, so the error a
        # client gets for naming somebody twice is nothing at all — a form
        # submitted twice is not two awards.
        attrs["employee_ids"] = list(dict.fromkeys(attrs["employee_ids"]))
        return attrs


# ─── Attendance ─────────────────────────────────────────────────────────────

ATTENDANCE_STATUSES = ("present", "absent", "late", "remote")


class AttendanceEntrySerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    position = serializers.CharField(allow_null=True, required=False)
    # Resolved to a URL by the view; null for anyone still on initials.
    photo = serializers.CharField(allow_null=True, required=False)
    department_name = serializers.CharField(allow_null=True, required=False)
    # Null for somebody nobody has accounted for yet today — which is a third
    # state, not the same as being marked absent.
    status = serializers.CharField(allow_null=True)
    checked_in_at = serializers.DateTimeField(allow_null=True, required=False)
    # Set once the employee files "Ketdim"; null for a day still open.
    checked_out_at = serializers.DateTimeField(allow_null=True, required=False)
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
    # The caller's own two stamps for the day. `my_checked_out_at` is what tells
    # the app whether to offer "Ketdim" or take the bar away entirely.
    my_checked_in_at = serializers.DateTimeField(allow_null=True, required=False)
    my_checked_out_at = serializers.DateTimeField(allow_null=True, required=False)
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


class AttendanceCheckOutSerializer(serializers.Serializer):
    """An employee marking the end of their day — "Ketdim".

    Coordinates are optional and never rejected on distance: the point of
    checking out is that the person is leaving, so being outside the geofence
    is the expected case, not a reason to refuse. They are stored, when sent,
    for the same audit reason the check-in pair is.
    """

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


class OwnProfileSerializer(serializers.Serializer):
    """The parts of their own entry somebody may rewrite.

    Two name fields rather than one, because that is what the account stores
    and what every other workspace will read it back as. `email` is blank-able:
    an address somebody no longer uses should be removable, and a form with no
    way to clear a field is a form that keeps stale contact details forever.

    Absent from this list, on purpose: the position, the department and the
    role — the workspace's description of the job, not the person's — and the
    phone, which is what the login is checked against.
    """

    first_name = serializers.CharField(max_length=100, trim_whitespace=True)
    last_name = serializers.CharField(
        max_length=100, required=False, allow_blank=True, trim_whitespace=True
    )
    email = serializers.EmailField(required=False, allow_blank=True)


# ─── Lending somebody to another workspace ────────────────────────────────────

class UsernameSerializer(serializers.Serializer):
    """The handle somebody picks for themselves.

    Deliberately narrow. This is a name other people type into a search box to
    find you, so it has to be typeable: lowercase letters, digits and
    underscore, no spaces, no punctuation to guess at. A leading "@" is
    accepted and dropped — it is how a handle is written, not part of it.

    Three characters minimum: shorter than that and the search matches half
    the company on the way to you.
    """

    username = serializers.CharField(max_length=50, allow_blank=True)

    def validate_username(self, value: str) -> str:
        handle = value.strip().lstrip("@").lower()
        # Blank clears it. Somebody who no longer wants a handle should not
        # have to keep one, and the column is nullable for that reason.
        if not handle:
            return ""
        if len(handle) < 3:
            raise serializers.ValidationError(
                _("At least 3 characters, please.")
            )
        if not re.fullmatch(r"[a-z0-9_]+", handle):
            raise serializers.ValidationError(
                _("Only lowercase letters, numbers and underscore.")
            )
        if handle[0].isdigit():
            # A handle that starts with a digit reads as an id rather than a
            # name, and "@12" next to a phone number is a coin toss.
            raise serializers.ValidationError(
                _("It has to start with a letter.")
            )
        return handle


class SecondmentRequestCreateSerializer(serializers.Serializer):
    """What "So'rov yuborish" posts.

    `ends_at` is optional. An open-ended secondment is a real thing — "come
    and help until we say otherwise" — and forcing a date would have people
    invent one, which is worse than storing that there isn't one.
    """

    to_employee_id = serializers.IntegerField()
    message = serializers.CharField(
        max_length=2000, allow_blank=True, required=False, trim_whitespace=True
    )
    role = serializers.ChoiceField(choices=RequestRole.CHOICES)
    modules = serializers.ListField(
        child=serializers.ChoiceField(choices=Module.CHOICES),
        required=False,
        allow_empty=True,
    )
    starts_at = serializers.DateTimeField(required=False, allow_null=True)
    ends_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        starts_at = attrs.get("starts_at")
        ends_at = attrs.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError(
                {"ends_at": _("The end has to come after the start.")}
            )
        if ends_at and ends_at <= timezone.now():
            raise serializers.ValidationError(
                {"ends_at": _("That is already in the past.")}
            )
        return attrs


class SecondmentDeclineSerializer(serializers.Serializer):
    """Saying no, and why.

    The reason is required. The whole point of declining rather than ignoring
    is that the workspace that asked learns something — "I am on leave", "ask
    me next week" — and a blank refusal tells them nothing they did not
    already know from the silence.
    """

    reason = serializers.CharField(max_length=1000, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError(_("Please say why."))
        return value.strip()


class SecondmentRequestSerializer(serializers.Serializer):
    """One row of the "So'rovlar" inbox, from either side."""

    id = serializers.IntegerField()
    company_id = serializers.IntegerField()
    company_name = serializers.CharField(allow_null=True, required=False)
    from_employee_id = serializers.IntegerField()
    from_full_name = serializers.CharField(allow_null=True, required=False)
    from_position = serializers.CharField(allow_null=True, required=False)
    from_photo = serializers.CharField(allow_null=True, required=False)
    to_employee_id = serializers.IntegerField()
    to_full_name = serializers.CharField(allow_null=True, required=False)
    to_position = serializers.CharField(allow_null=True, required=False)
    to_photo = serializers.CharField(allow_null=True, required=False)
    to_company_name = serializers.CharField(allow_null=True, required=False)
    message = serializers.CharField(allow_blank=True, required=False)
    role = serializers.CharField()
    modules = serializers.ListField(child=serializers.CharField(), required=False)
    starts_at = serializers.DateTimeField(allow_null=True, required=False)
    ends_at = serializers.DateTimeField(allow_null=True, required=False)
    status = serializers.CharField()
    decline_reason = serializers.CharField(allow_null=True, required=False)
    responded_at = serializers.DateTimeField(allow_null=True, required=False)
    created_at = serializers.DateTimeField(required=False)


class OrgPersonSerializer(serializers.Serializer):
    """Somebody in a sibling workspace, as the picker lists them.

    Carries the workspace they belong to, which the picker inside one
    workspace never had to: "Aziz Karimov" is not enough to pick from when the
    org has four of them in four offices.
    """

    id = serializers.IntegerField()
    full_name = serializers.CharField()
    username = serializers.CharField(allow_null=True, required=False)
    position = serializers.CharField(allow_null=True, required=False)
    phone = serializers.CharField(allow_null=True, required=False)
    photo = serializers.CharField(allow_null=True, required=False)
    role = serializers.CharField(required=False)
    company_id = serializers.IntegerField()
    company_name = serializers.CharField(allow_null=True, required=False)


# ─── Hisobot va analitika ───────────────────────────────────────────────────
#
# Read-only, and documentation as much as validation: nothing here is ever
# fed a request body. What they buy is a generated client that knows the shape
# of a report instead of `Map<String, dynamic>` all the way down.


class ReportPeriodSerializer(serializers.Serializer):
    """The window the numbers below were counted over."""

    period = serializers.CharField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    #: How wide one point on a trend line is — "1 day", "1 week", "1 month".
    #: Sent so the chart can label its axis without re-deriving it from the
    #: period name.
    bucket = serializers.CharField()


class SalesStagePointSerializer(serializers.Serializer):
    stage = serializers.CharField()
    count = serializers.IntegerField()
    amount = serializers.CharField()


class SalesSourcePointSerializer(serializers.Serializer):
    source = serializers.CharField()
    count = serializers.IntegerField()
    won_count = serializers.IntegerField()
    won_amount = serializers.CharField()


class LostReasonPointSerializer(serializers.Serializer):
    reason = serializers.CharField()
    count = serializers.IntegerField()


class SalesTrendPointSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    created_count = serializers.IntegerField()
    won_count = serializers.IntegerField()
    won_amount = serializers.CharField()


class SalesLeaderSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    photo = serializers.CharField(allow_null=True, required=False)
    won_count = serializers.IntegerField()
    won_amount = serializers.CharField()


class SalesReportSerializer(serializers.Serializer):
    """The funnel over the window.

    Amounts are strings, not numbers: they are NUMERIC(14, 2) in so'm, and a
    deal of eleven figures does not survive a JSON float intact.
    """

    created_count = serializers.IntegerField()
    won_count = serializers.IntegerField()
    lost_count = serializers.IntegerField()
    #: As of now, not windowed — a pipeline is a present-tense fact.
    open_count = serializers.IntegerField()
    won_amount = serializers.CharField()
    open_amount = serializers.CharField()
    #: Won ÷ decided (won + lost), 0–1. Deals still being worked are in
    #: neither half: one that has not been answered has not been lost.
    conversion_rate = serializers.FloatField()
    average_deal = serializers.CharField()
    by_stage = SalesStagePointSerializer(many=True)
    by_source = SalesSourcePointSerializer(many=True)
    lost_reasons = LostReasonPointSerializer(many=True)
    trend = SalesTrendPointSerializer(many=True)
    leaders = SalesLeaderSerializer(many=True)


class TaskPriorityPointSerializer(serializers.Serializer):
    priority = serializers.CharField()
    count = serializers.IntegerField()


class TaskTrendPointSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    created_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()


class TaskLeaderSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField()
    photo = serializers.CharField(allow_null=True, required=False)
    completed_count = serializers.IntegerField()
    on_time_rate = serializers.FloatField()


class TaskReportSerializer(serializers.Serializer):
    created_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()
    open_count = serializers.IntegerField()
    todo_count = serializers.IntegerField()
    in_progress_count = serializers.IntegerField()
    overdue_count = serializers.IntegerField()
    due_today_count = serializers.IntegerField()
    #: Out of the completed tasks that had a deadline, 0–1.
    on_time_rate = serializers.FloatField()
    by_priority = TaskPriorityPointSerializer(many=True)
    trend = TaskTrendPointSerializer(many=True)
    leaders = TaskLeaderSerializer(many=True)


class EventTypePointSerializer(serializers.Serializer):
    event_type = serializers.CharField()
    count = serializers.IntegerField()


class EventWeekdayPointSerializer(serializers.Serializer):
    #: ISO weekday, 1 = Monday, so the week reads the way the calendar screen
    #: draws it.
    weekday = serializers.IntegerField()
    count = serializers.IntegerField()


class EventTrendPointSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    count = serializers.IntegerField()


class CalendarReportSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    all_day_count = serializers.IntegerField()
    #: Everything still to come, from the end of the window onwards.
    upcoming_count = serializers.IntegerField()
    #: Hours booked, all-day entries excluded — see `repository.calendar_report`.
    hours = serializers.FloatField()
    by_type = EventTypePointSerializer(many=True)
    by_weekday = EventWeekdayPointSerializer(many=True)
    trend = EventTrendPointSerializer(many=True)


class WorkspaceReportSerializer(serializers.Serializer):
    """Everything the "Hisobot va analitika" screen draws, in one response.

    A section is null when this caller cannot see it at all — a guest who was
    lent the sales board and nothing else gets `sales` and two nulls, rather
    than three sections of zeroes that would read as a quiet company.
    """

    period = ReportPeriodSerializer()
    #: `company` for a manager, `own` for everybody else — what the numbers
    #: cover, printed on the screen so nobody reads their own month as the
    #: company's.
    scope = serializers.CharField()
    sales = SalesReportSerializer(allow_null=True)
    tasks = TaskReportSerializer(allow_null=True)
    calendar = CalendarReportSerializer(allow_null=True)
