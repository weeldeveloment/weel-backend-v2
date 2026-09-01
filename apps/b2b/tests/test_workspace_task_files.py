"""Documents attached to a task.

A task's brief, its annexes, a photographed receipt — posted straight after the
task itself, the same way its voice note is. Two things are worth pinning down
here: who may spend the company's quota on somebody else's task, and that a
document and a recording never get mistaken for one another, since both are
rows in one storage table keyed by the same `task_id`.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceTaskFileDetailView,
    WorkspaceTaskFilesView,
    _task_payload,
)

factory = APIRequestFactory()

AUTHOR = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Muallif",
    "phone": "+998900000001",
})

ASSIGNEE = WorkspaceUser({
    "id": 8,
    "company_id": 55,
    "role": "employee",
    "full_name": "Mas'ul",
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
        "files": [],
    }
    row.update(overrides)
    return row


def _document(name="brief.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4", content_type="application/pdf")


def _post(user, task=None):
    request = factory.post("/tasks/3/files/", {"file": _document()})
    force_authenticate(request, user=user)
    with patch("apps.b2b.workspace.views.repo") as repo, patch(
        "apps.b2b.workspace.views.store_upload"
    ) as store, patch("apps.b2b.workspace.views.default_storage") as storage:
        repo.get_task.return_value = _task() if task is None else task
        repo.TASK_FILE_KIND = "task_file"
        store.return_value = ({"id": 1}, None)
        storage.url.return_value = "https://cdn.test/brief.pdf"
        response = WorkspaceTaskFilesView.as_view()(request, task_id=3)
    return response, repo, store, storage


class TestAttaching:
    def test_the_author_may_attach_a_document(self):
        response, _repo, store, _storage = _post(AUTHOR)

        assert response.status_code == 201
        assert store.call_args.kwargs["task_id"] == 3
        # Its own kind, not the voice note's: the drive lists kind='file' and
        # the task payload tells a clip from a document by this column alone.
        assert store.call_args.kwargs["kind"] == "task_file"

    def test_an_assignee_may_attach_a_document(self):
        response, _repo, _store, _storage = _post(ASSIGNEE)
        assert response.status_code == 201

    def test_a_manager_may_attach_to_anybody_s_task(self):
        response, _repo, _store, _storage = _post(
            MANAGER, task=_task(author_id=1, assignee_ids=[2])
        )
        assert response.status_code == 201

    def test_a_bystander_s_attach_is_a_404_not_a_403(self):
        request = factory.post("/tasks/3/files/", {"file": _document()})
        force_authenticate(request, user=ASSIGNEE)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.store_upload"
        ) as store:
            repo.get_task.return_value = _task(author_id=1, assignee_ids=[2])
            response = WorkspaceTaskFilesView.as_view()(request, task_id=3)

        assert response.status_code == 404
        store.assert_not_called()

    def test_a_post_with_no_file_is_refused(self):
        request = factory.post("/tasks/3/files/", {})
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_task.return_value = _task()
            response = WorkspaceTaskFilesView.as_view()(request, task_id=3)

        assert response.status_code == 400

    def test_a_refused_upload_is_returned_as_is(self):
        # Out of quota comes back from store_upload as a ready response; the
        # view must not paper over it with a 201.
        request = factory.post("/tasks/3/files/", {"file": _document()})
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.store_upload"
        ) as store, patch("apps.b2b.workspace.views.default_storage"):
            repo.get_task.return_value = _task()
            store.return_value = (None, Response({"detail": "full"}, status=413))
            response = WorkspaceTaskFilesView.as_view()(request, task_id=3)

        assert response.status_code == 413

    def test_attaching_never_replaces_what_is_already_there(self):
        # The voice endpoint drops the previous clip on purpose. Documents
        # accumulate: a brief and its annex are two files, and "replace" would
        # be a data loss dressed up as a correction.
        _response, repo, _store, _storage = _post(AUTHOR)
        repo.delete_task_file.assert_not_called()
        repo.delete_task_voice.assert_not_called()


class TestDetaching:
    def _delete(self, user, task=None, removed=None):
        request = factory.delete("/tasks/3/files/4/")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.default_storage"
        ) as storage:
            repo.get_task.return_value = _task() if task is None else task
            repo.delete_task_file.return_value = removed
            response = WorkspaceTaskFileDetailView.as_view()(
                request, task_id=3, file_id=4
            )
        return response, repo, storage

    def test_detaching_takes_the_row_and_the_object(self):
        response, repo, storage = self._delete(
            AUTHOR, removed={"id": 4, "path": "b2b/brief.pdf"}
        )

        assert response.status_code == 200
        repo.delete_task_file.assert_called_once_with(3, 4)
        storage.delete.assert_called_once_with("b2b/brief.pdf")

    def test_a_file_that_is_not_on_this_task_is_a_404(self):
        # The id comes off the URL; without the task scope a caller could name
        # any row in the table and have its bytes deleted.
        response, _repo, storage = self._delete(AUTHOR, removed=None)

        assert response.status_code == 404
        storage.delete.assert_not_called()

    def test_a_bystander_may_not_detach(self):
        response, repo, _storage = self._delete(
            ASSIGNEE, task=_task(author_id=1, assignee_ids=[2])
        )

        assert response.status_code == 404
        repo.delete_task_file.assert_not_called()


class TestPayload:
    def test_the_task_carries_fetchable_urls_not_storage_paths(self):
        with patch("apps.b2b.workspace.views.default_storage") as storage:
            storage.url.return_value = "https://cdn.test/brief.pdf"
            payload = _task_payload(
                _task(files=[{
                    "id": 4,
                    "name": "brief.pdf",
                    "size": 1234,
                    "path": "b2b/workspace/55/task_file/brief.pdf",
                    "content_type": "application/pdf",
                }]),
                AUTHOR,
            )

        assert payload["files"][0]["url"] == "https://cdn.test/brief.pdf"
        assert payload["files"][0]["name"] == "brief.pdf"
        assert "path" not in payload["files"][0]

    def test_a_task_with_nothing_attached_carries_an_empty_list(self):
        # Not a missing key: every client would then need its own fallback.
        payload = _task_payload(_task(), AUTHOR)
        assert payload["files"] == []
