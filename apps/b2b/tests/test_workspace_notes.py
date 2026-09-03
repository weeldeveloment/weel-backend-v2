"""Quick notes: the strip above the calendar.

A note is somebody's own by default. That is the rule these pin down, because
it is the one that makes the feature usable — a manager who could read every
note in the company would turn the place people scribble into another board
they are watched on — and it is the rule most easily lost, since every other
list in the workspace widens with role.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.serializers import NotePatchSerializer, NoteWriteSerializer
from apps.b2b.workspace.views import (
    WorkspaceNoteDetailView,
    WorkspaceNoteListCreateView,
    WorkspaceNoteVoiceView,
)

factory = APIRequestFactory()

AUTHOR = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Xodim",
    "phone": "+998900000001",
})

OWNER = WorkspaceUser({
    "id": 1,
    "company_id": 55,
    "role": "owner",
    "full_name": "Rahbar",
    "phone": "+998900000002",
})


def _note(**overrides):
    row = {
        "id": 4,
        "company_id": 55,
        "author_id": 7,
        "kind": "text",
        "title": "Loyiha rejasi",
        "body": "3-chorak drayverlari va KPI ko'rsatkichlari.",
        "color": "green",
        "is_pinned": False,
        "is_shared": False,
        "voice": None,
    }
    row.update(overrides)
    return row


class TestWhatAValidWriteLooksLike:
    def test_a_note_needs_neither_a_title_nor_a_body(self):
        # A voice note is created empty and the recording is posted straight
        # after, so an endpoint that demanded text could not save one at all.
        assert NoteWriteSerializer(data={"kind": "voice"}).is_valid()

    def test_only_the_six_colours_the_picker_draws(self):
        assert repo.NOTE_COLORS == ("green", "violet", "blue", "orange", "pink", "red")
        assert not NoteWriteSerializer(data={"color": "teal"}).is_valid()

    @pytest.mark.parametrize("color", repo.NOTE_COLORS)
    def test_each_of_them_is_accepted(self, color):
        assert NoteWriteSerializer(data={"color": color}).is_valid()

    def test_pinning_is_a_patch_and_never_part_of_a_create(self):
        # Nothing arrives pinned: the strip's first slot is something somebody
        # chose to put there, not something a client could claim on create.
        assert "is_pinned" not in NoteWriteSerializer().fields
        assert NotePatchSerializer(data={"is_pinned": True}).is_valid()


class TestWhoSeesWhat:
    def test_the_list_is_scoped_to_the_caller_and_not_to_their_role(self):
        request = factory.get("/notes/")
        force_authenticate(request, user=OWNER)

        with patch("apps.b2b.workspace.views.repo") as mocked:
            mocked.list_notes.return_value = []
            WorkspaceNoteListCreateView.as_view()(request)

        # The owner asks for their own notes, exactly as an employee does.
        mocked.list_notes.assert_called_once_with(55, employee_id=1)

    def test_the_query_asks_for_your_own_plus_anything_shared(self):
        with patch("apps.b2b.workspace.repository.fetch_all", return_value=[]) as fetch_all:
            repo.list_notes(55, employee_id=7)

        sql = fetch_all.call_args.args[0]
        assert "author_id = %s OR is_shared" in sql
        # Pinned first, then most recently touched — the order the strip draws.
        assert "ORDER BY is_pinned DESC, updated_at DESC" in sql

    def test_a_note_you_did_not_write_is_not_yours_to_edit(self):
        request = factory.patch("/notes/4/", {"title": "Boshqa nom"}, format="json")
        force_authenticate(request, user=OWNER)

        with patch("apps.b2b.workspace.views.repo") as mocked:
            mocked.get_note.return_value = _note(is_shared=True)
            response = WorkspaceNoteDetailView.as_view()(request, note_id=4)

        # 404 rather than 403: the owner can read this shared note, and a
        # refusal has no reason to confirm which ids are writable.
        assert response.status_code == 404
        mocked.update_note.assert_not_called()

    def test_can_edit_says_so_on_the_way_out(self):
        request = factory.get("/notes/")
        force_authenticate(request, user=OWNER)

        with patch("apps.b2b.workspace.views.repo") as mocked:
            mocked.list_notes.return_value = [_note(is_shared=True), _note(id=5, author_id=1)]
            response = WorkspaceNoteListCreateView.as_view()(request)

        assert [row["can_edit"] for row in response.data] == [False, True]


class TestDeleting:
    def test_the_recording_goes_with_the_note(self):
        # The file row cascades; the object does not, and bytes nothing can
        # reach still count against the company's quota.
        request = factory.delete("/notes/4/")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as mocked, \
             patch("apps.b2b.workspace.views.default_storage") as store:
            mocked.get_note.return_value = _note(kind="voice")
            mocked.delete_note.return_value = _note(
                kind="voice", voice={"id": 9, "path": "b2b/workspace/55/note/a.m4a"}
            )
            response = WorkspaceNoteDetailView.as_view()(request, note_id=4)

        assert response.status_code == 204
        store.delete.assert_called_once_with("b2b/workspace/55/note/a.m4a")

    def test_a_text_note_deletes_without_touching_storage(self):
        request = factory.delete("/notes/4/")
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as mocked, \
             patch("apps.b2b.workspace.views.default_storage") as store:
            mocked.get_note.return_value = _note()
            mocked.delete_note.return_value = _note()
            response = WorkspaceNoteDetailView.as_view()(request, note_id=4)

        assert response.status_code == 204
        store.delete.assert_not_called()


class TestTheRecording:
    def test_a_second_clip_replaces_the_first(self):
        # A note carries one recording. Re-recording is the common case, and
        # leaving the earlier attempt on the company's quota is not what
        # "replace" means.
        upload = SimpleUploadedFile("eslatma.m4a", b"aac", content_type="audio/mp4")
        request = factory.post("/notes/4/voice/", {"file": upload, "duration_ms": "12000"})
        force_authenticate(request, user=AUTHOR)

        with patch("apps.b2b.workspace.views.repo") as mocked, \
             patch("apps.b2b.workspace.views.store_upload", return_value=({}, None)) as store:
            mocked.NOTE_VOICE_KIND = repo.NOTE_VOICE_KIND
            mocked.get_note.return_value = _note(kind="voice")
            mocked.delete_note_voice.return_value = None
            response = WorkspaceNoteVoiceView.as_view()(request, note_id=4)

        assert response.status_code == 201
        mocked.delete_note_voice.assert_called_once_with(4)
        # Filed under its own kind so the drive does not list it as a document
        # and the quota breakdown can say how much notes are costing.
        assert store.call_args.kwargs["kind"] == "note"
        assert store.call_args.kwargs["note_id"] == 4
        # From the recorder: the server never opens the file to time it.
        assert store.call_args.kwargs["duration_ms"] == 12000

    def test_only_the_author_may_attach_one(self):
        upload = SimpleUploadedFile("eslatma.m4a", b"aac", content_type="audio/mp4")
        request = factory.post("/notes/4/voice/", {"file": upload})
        force_authenticate(request, user=OWNER)

        with patch("apps.b2b.workspace.views.repo") as mocked, \
             patch("apps.b2b.workspace.views.store_upload") as store:
            mocked.get_note.return_value = _note(is_shared=True)
            response = WorkspaceNoteVoiceView.as_view()(request, note_id=4)

        assert response.status_code == 404
        store.assert_not_called()

    def test_the_clip_is_looked_up_by_kind_and_not_only_by_note(self):
        # Every upload the workspace stores lives in one table. Without the
        # kind filter, any other file that ever pointed at this note would be
        # handed back as its recording — and then deleted by the endpoint that
        # replaces one.
        with patch("apps.b2b.workspace.repository.fetch_one", return_value=None) as fetch_one:
            repo.note_voice(4)

        sql, params = fetch_one.call_args.args
        assert "note_id = %s AND kind = %s" in sql
        assert params == [4, "note"]
