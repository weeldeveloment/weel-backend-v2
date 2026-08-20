"""A voice note attached to a task.

The clip is recorded while the task is being written and posted straight after
the task itself, so this endpoint is the second half of "create a task" — which
makes the two things worth pinning down: who is allowed to attach one, and that
re-recording does not quietly leave the first attempt on the company's quota.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import WorkspaceTaskVoiceView

factory = APIRequestFactory()

AUTHOR = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Muallif",
    "phone": "+998900000001",
})

OUTSIDER = WorkspaceUser({
    "id": 8,
    "company_id": 55,
    "role": "employee",
    "full_name": "Begona",
    "phone": "+998900000002",
})

MANAGER = WorkspaceUser({
    "id": 9,
    "company_id": 55,
    "role": "owner",
    "full_name": "Rahbar",
    "phone": "+998900000003",
})


def _task(**overrides):
    row = {
        "id": 3,
        "company_id": 55,
        "author_id": 7,
        "title": "Vazifa",
        "assignee_ids": [8],
        "subtasks": [],
        "comments": [],
        "voice": None,
    }
    row.update(overrides)
    return row


def _clip():
    return SimpleUploadedFile("voice.m4a", b"audio", content_type="audio/mp4")


def _post(user, task=None, duration_ms="7400"):
    request = factory.post(
        "/tasks/3/voice/", {"file": _clip(), "duration_ms": duration_ms}
    )
    force_authenticate(request, user=user)
    with patch("apps.b2b.workspace.views.repo") as repo, patch(
        "apps.b2b.workspace.views.store_upload"
    ) as store, patch("apps.b2b.workspace.views.default_storage") as storage:
        repo.get_task.return_value = _task() if task is None else task
        repo.delete_task_voice.return_value = None
        store.return_value = ({"id": 1}, None)
        storage.url.return_value = "https://cdn.test/voice.m4a"
        response = WorkspaceTaskVoiceView.as_view()(request, task_id=3)
    return response, repo, store, storage


class TestAttaching:
    def test_the_author_may_attach_a_clip(self):
        response, _repo, store, _storage = _post(AUTHOR)

        assert response.status_code == 201
        assert store.call_args.kwargs["task_id"] == 3
        assert store.call_args.kwargs["kind"] == "task"
        # Straight from the recorder — the server never decodes the audio.
        assert store.call_args.kwargs["duration_ms"] == 7400

    def test_an_assignee_may_attach_a_clip(self):
        response, _repo, _store, _storage = _post(OUTSIDER)
        assert response.status_code == 201

    def test_a_manager_may_attach_to_anybody_s_task(self):
        response, _repo, _store, _storage = _post(
            MANAGER, task=_task(author_id=1, assignee_ids=[2])
        )
        assert response.status_code == 201

    def test_somebody_else_s_task_is_a_404_not_a_403(self):
        # An employee who is neither author nor assignee must not even learn
        # that this task id exists.
        request = factory.post("/tasks/3/voice/", {"file": _clip()})
        force_authenticate(request, user=OUTSIDER)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_task.return_value = _task(author_id=1, assignee_ids=[2])
            response = WorkspaceTaskVoiceView.as_view()(request, task_id=3)

        assert response.status_code == 404
        repo.delete_task_voice.assert_not_called()

    def test_a_post_with_no_file_is_refused(self):
        request = factory.post("/tasks/3/voice/", {})
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_task.return_value = _task()
            response = WorkspaceTaskVoiceView.as_view()(request, task_id=3)

        assert response.status_code == 400

    def test_a_nonsense_duration_costs_the_label_not_the_clip(self):
        _response, _repo, store, _storage = _post(AUTHOR, duration_ms="uzun")
        assert store.call_args.kwargs["duration_ms"] is None


class TestReplacing:
    def test_re_recording_deletes_the_previous_clip(self):
        # Both halves: the row, so the quota stops counting it, and the object,
        # so the bytes are actually gone.
        request = factory.post("/tasks/3/voice/", {"file": _clip()})
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.store_upload"
        ) as store, patch("apps.b2b.workspace.views.default_storage") as storage:
            repo.get_task.return_value = _task()
            repo.delete_task_voice.return_value = {"id": 4, "path": "b2b/old.m4a"}
            store.return_value = ({"id": 5}, None)
            WorkspaceTaskVoiceView.as_view()(request, task_id=3)

        repo.delete_task_voice.assert_called_once_with(3)
        storage.delete.assert_called_once_with("b2b/old.m4a")

    def test_a_refused_upload_is_returned_as_is(self):
        # Out of quota comes back from store_upload as a ready response; the
        # view must not paper over it with a 201.
        from rest_framework.response import Response

        request = factory.post("/tasks/3/voice/", {"file": _clip()})
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.store_upload"
        ) as store, patch("apps.b2b.workspace.views.default_storage"):
            repo.get_task.return_value = _task()
            repo.delete_task_voice.return_value = None
            store.return_value = (None, Response({"detail": "full"}, status=413))
            response = WorkspaceTaskVoiceView.as_view()(request, task_id=3)

        assert response.status_code == 413


class TestRemoving:
    def test_delete_takes_the_row_and_the_object(self):
        request = factory.delete("/tasks/3/voice/")
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.default_storage"
        ) as storage:
            repo.get_task.return_value = _task()
            repo.delete_task_voice.return_value = {"id": 4, "path": "b2b/old.m4a"}
            response = WorkspaceTaskVoiceView.as_view()(request, task_id=3)

        assert response.status_code == 200
        storage.delete.assert_called_once_with("b2b/old.m4a")


class TestPayload:
    def test_the_task_carries_a_playable_url_not_a_storage_path(self):
        from apps.b2b.workspace.views import _task_payload

        with patch("apps.b2b.workspace.views.default_storage") as storage:
            storage.url.return_value = "https://cdn.test/voice.m4a"
            payload = _task_payload(
                _task(voice={
                    "id": 4,
                    "name": "voice.m4a",
                    "size": 1234,
                    "path": "b2b/workspace/55/task/voice.m4a",
                    "content_type": "audio/mp4",
                    "duration_ms": 7400,
                }),
                AUTHOR,
            )

        assert payload["voice"]["url"] == "https://cdn.test/voice.m4a"
        assert payload["voice"]["duration_ms"] == 7400
        assert "path" not in payload["voice"]

    def test_a_task_with_no_clip_says_so(self):
        from apps.b2b.workspace.views import _task_payload

        assert _task_payload(_task(), AUTHOR)["voice"] is None
