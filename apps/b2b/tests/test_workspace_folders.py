"""Folders on the shared drive.

The drive was one flat list per company; a folder is somebody's arrangement of
it. Two things matter and both are about not confusing the two: deleting a
folder must not delete the documents in it, and a folder id from another
company must not be a way to read or write into that company's drive.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceFileDetailView,
    WorkspaceFileListCreateView,
    WorkspaceFolderDetailView,
    WorkspaceFolderListCreateView,
)

factory = APIRequestFactory()

AUTHOR = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Xodim",
    "phone": "+998900000001",
})

COLLEAGUE = WorkspaceUser({
    "id": 8,
    "company_id": 55,
    "role": "employee",
    "full_name": "Hamkasb",
    "phone": "+998900000002",
})

OWNER = WorkspaceUser({
    "id": 9,
    "company_id": 55,
    "role": "owner",
    "full_name": "Rahbar",
    "phone": "+998900000003",
})


def _folder(**overrides):
    row = {
        "id": 3,
        "company_id": 55,
        "author_id": 7,
        "name": "Shartnomalar",
        "created_at": "2026-08-20T10:00:00Z",
    }
    row.update(overrides)
    return row


class TestCreating:
    def test_anyone_in_the_company_may_make_one(self):
        # The drive is shared and anyone may add a file to it; arranging it is
        # the same kind of act.
        request = factory.post("/folders/", {"name": "Shartnomalar"}, format="json")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.create_folder.return_value = _folder()
            response = WorkspaceFolderListCreateView.as_view()(request)

        assert response.status_code == 201
        assert repo.create_folder.call_args.kwargs["company_id"] == 55
        assert repo.create_folder.call_args.kwargs["author_id"] == 7
        # A brand new folder answers with the same shape the list does, counts
        # and all, rather than leaving the client to invent them.
        assert response.data["file_count"] == 0
        assert response.data["size_bytes"] == 0

    def test_a_nameless_folder_is_refused(self):
        request = factory.post("/folders/", {"name": "   "}, format="json")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo:
            response = WorkspaceFolderListCreateView.as_view()(request)

        assert response.status_code == 400
        repo.create_folder.assert_not_called()


class TestDeleting:
    def test_the_person_who_made_it_may_delete_it(self):
        request = factory.delete("/folders/3/")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_folder.return_value = _folder()
            response = WorkspaceFolderDetailView.as_view()(request, folder_id=3)

        assert response.status_code == 204
        repo.delete_folder.assert_called_once_with(3, 55)

    def test_a_manager_may_delete_anybody_s(self):
        request = factory.delete("/folders/3/")
        force_authenticate(request, user=OWNER)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_folder.return_value = _folder()
            response = WorkspaceFolderDetailView.as_view()(request, folder_id=3)

        assert response.status_code == 204

    def test_a_colleague_may_not(self):
        request = factory.delete("/folders/3/")
        force_authenticate(request, user=COLLEAGUE)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_folder.return_value = _folder()
            response = WorkspaceFolderDetailView.as_view()(request, folder_id=3)

        assert response.status_code == 403
        repo.delete_folder.assert_not_called()

    def test_another_company_s_folder_is_not_there(self):
        request = factory.delete("/folders/3/")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo:
            # The lookup is scoped to the company, so somebody else's id comes
            # back empty rather than deletable.
            repo.get_folder.return_value = None
            response = WorkspaceFolderDetailView.as_view()(request, folder_id=3)

        assert response.status_code == 404
        repo.delete_folder.assert_not_called()


class TestListingFiles:
    def test_a_folder_narrows_the_drive_to_its_contents(self):
        request = factory.get("/files/?folder_id=3")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.default_storage"
        ):
            repo.get_folder.return_value = _folder()
            repo.list_files.return_value = []
            WorkspaceFileListCreateView.as_view()(request)

        # By folder, not by kind: a folder holds whatever somebody put in it.
        assert repo.list_files.call_args.kwargs == {"folder_id": 3}

    def test_a_folder_that_is_not_this_company_s_is_a_404(self):
        request = factory.get("/files/?folder_id=3")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_folder.return_value = None
            response = WorkspaceFileListCreateView.as_view()(request)

        assert response.status_code == 404
        repo.list_files.assert_not_called()


class TestUploadingIntoOne:
    def test_the_file_is_recorded_in_the_folder(self):
        request = factory.post(
            "/files/",
            {"file": SimpleUploadedFile("a.pdf", b"pdf"), "folder_id": "3"},
        )
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.store_upload"
        ) as store, patch("apps.b2b.workspace.views.default_storage"):
            repo.get_folder.return_value = _folder()
            store.return_value = ({"id": 1, "path": "p"}, None)
            response = WorkspaceFileListCreateView.as_view()(request)

        assert response.status_code == 201
        assert store.call_args.kwargs["folder_id"] == 3

    def test_an_unknown_folder_refuses_the_upload(self):
        request = factory.post(
            "/files/",
            {"file": SimpleUploadedFile("a.pdf", b"pdf"), "folder_id": "999"},
        )
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.store_upload"
        ) as store:
            repo.get_folder.return_value = None
            response = WorkspaceFileListCreateView.as_view()(request)

        assert response.status_code == 400
        store.assert_not_called()


class TestRenamingAndMoving:
    def _patch(self, user, body, folder=None):
        request = factory.patch("/files/9/", body, format="json")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.default_storage"
        ) as storage:
            repo.get_file.return_value = {"id": 9, "path": "p", "name": "eski.pdf"}
            repo.get_folder.return_value = folder
            repo.update_file.return_value = {"id": 9, "path": "p", "name": "yangi.pdf"}
            storage.url.return_value = "https://cdn.test/p"
            response = WorkspaceFileDetailView.as_view()(request, file_id=9)
        return response, repo

    def test_a_rename_writes_the_name(self):
        response, repo = self._patch(AUTHOR, {"name": "yangi.pdf"})

        assert response.status_code == 200
        assert repo.update_file.call_args.kwargs == {"name": "yangi.pdf"}

    def test_a_move_writes_the_folder(self):
        response, repo = self._patch(AUTHOR, {"folder_id": 3}, folder=_folder())

        assert response.status_code == 200
        assert repo.update_file.call_args.kwargs == {"folder_id": 3}

    def test_moving_to_a_folder_in_another_company_is_refused(self):
        # A folder id is a way into the drive; it is checked before anything
        # is written.
        response, repo = self._patch(AUTHOR, {"folder_id": 3}, folder=None)

        assert response.status_code == 400
        repo.update_file.assert_not_called()

    def test_null_takes_the_file_back_to_the_drive(self):
        # The one folder value that needs no lookup: it means no folder.
        response, repo = self._patch(AUTHOR, {"folder_id": None})

        assert response.status_code == 200
        assert repo.update_file.call_args.kwargs == {"folder_id": None}

    def test_a_file_from_another_company_is_not_there(self):
        request = factory.patch("/files/9/", {"name": "yangi.pdf"}, format="json")
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_file.return_value = None
            response = WorkspaceFileDetailView.as_view()(request, file_id=9)

        assert response.status_code == 404
        repo.update_file.assert_not_called()


class TestRenamingCannotChangeTheType:
    """A rename may not change what kind of file something is.

    The extension is how every reader on the other side decides how to open the
    bytes, and a rename does not rewrite the bytes: a .xlsx called .pdf is a
    file that opens nowhere. Enforced here rather than only in the app's own
    field — this endpoint is reachable without it.
    """

    def _rename(self, requested, current="hisobot.xlsx"):
        request = factory.patch("/files/9/", {"name": requested}, format="json")
        force_authenticate(request, user=AUTHOR)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.default_storage"
        ) as storage:
            repo.get_file.return_value = {"id": 9, "path": "p", "name": current}
            repo.update_file.return_value = {"id": 9, "path": "p", "name": "x"}
            storage.url.return_value = "https://cdn.test/p"
            WorkspaceFileDetailView.as_view()(request, file_id=9)
        return repo.update_file.call_args.kwargs["name"]

    def test_a_different_extension_is_put_back(self):
        assert self._rename("hisobot.pdf") == "hisobot.xlsx"

    def test_a_name_with_no_extension_gets_the_original(self):
        assert self._rename("yangi nom") == "yangi nom.xlsx"

    def test_the_stem_is_what_actually_changes(self):
        assert self._rename("2026 yillik hisobot.xlsx") == "2026 yillik hisobot.xlsx"

    def test_a_file_that_never_had_one_is_left_alone(self):
        assert self._rename("README", current="LICENSE") == "README"

    def test_dropping_the_extension_entirely_is_refused(self):
        # ".pdf" as a whole name would leave a file with no readable name at
        # all; the stem falls back to the one it had.
        assert self._rename(".pdf") == "hisobot.xlsx"
