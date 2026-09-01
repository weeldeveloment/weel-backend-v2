"""Task titles are not capped.

The mobile sheet has no title box: it titles a task by the first line of the
description. A first line that runs long is ordinary writing, and the old
VARCHAR(300)-shaped cap rejected the request — losing everything typed. The
column is TEXT now, and these pin the serializers to that.
"""
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.workspace.serializers import TaskPatchSerializer, TaskWriteSerializer

LONG = ("Vazifa " * 500).strip()  # ~3500 characters


@pytest.mark.parametrize("serializer_class", [TaskWriteSerializer, TaskPatchSerializer])
def test_long_title_is_accepted(serializer_class):
    serializer = serializer_class(data={"title": LONG})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["title"] == LONG


@pytest.mark.parametrize("serializer_class", [TaskWriteSerializer, TaskPatchSerializer])
def test_long_subtask_is_accepted(serializer_class):
    serializer = serializer_class(data={"title": "Qisqa", "subtasks": [LONG]})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["subtasks"] == [LONG]
