from __future__ import annotations

from rest_framework import serializers

from apps.b2b.workspace.repository import EVENT_TYPES, TASK_PRIORITIES, TASK_STATUSES


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
    text = serializers.CharField()
    created_at = serializers.DateTimeField()

    class Meta:
        # `chat.ChatMessageSerializer` is a different shape (conversation_id,
        # content, read flags). Without distinct ref_names drf_yasg refuses to
        # build the combined schema at all.
        ref_name = "WorkspaceChatMessage"


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
    text = serializers.CharField(max_length=4000)

    def validate_text(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Message cannot be empty.")
        return value


class ThreadFlagsSerializer(serializers.Serializer):
    is_pinned = serializers.BooleanField(required=False)
    is_muted = serializers.BooleanField(required=False)
