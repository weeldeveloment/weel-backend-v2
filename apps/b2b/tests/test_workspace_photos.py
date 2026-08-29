"""A stored picture reaches the client as a URL, everywhere it is sent.

The columns hold a *path* — only the server knows which backend the bytes are
on. That is the right thing to store and the wrong thing to send, and the two
were mixed up: uploading an avatar wrote the path correctly and ``/me/``
resolved it, while the roster shipped the bare path. The effect was an avatar
that appeared on its owner's profile screen and as initials in every list,
chat row and group — which reads exactly like the upload having failed.

So these do not test the upload. They test the resolution, at each payload
that carries a picture, because that is the part that was wrong in four places
and right in one.
"""
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import storage
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import WorkspaceTeamView

factory = APIRequestFactory()

EMPLOYEE = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Xodim",
    "phone": "+998900000001",
})


class TestPhotoUrl:
    def test_a_stored_path_becomes_a_url(self):
        with patch("apps.b2b.workspace.storage.default_storage") as store:
            store.url.return_value = "https://cdn/media/b2b/55/avatar/a.jpg"
            assert (
                storage.photo_url("b2b/workspace/55/avatar/a.jpg")
                == "https://cdn/media/b2b/55/avatar/a.jpg"
            )

    def test_an_absolute_url_is_left_alone(self):
        # A row written before the upload endpoint existed, or a face that came
        # from somewhere else entirely. Running it through the storage backend
        # would produce a URL pointing at a file that is not there.
        with patch("apps.b2b.workspace.storage.default_storage") as store:
            assert (
                storage.photo_url("https://lh3.googleusercontent.com/a/x")
                == "https://lh3.googleusercontent.com/a/x"
            )
            assert not store.url.called

    def test_no_photo_stays_no_photo(self):
        # The avatar falls back to initials on null, so an empty string must
        # not be turned into a URL that 404s.
        assert storage.photo_url(None) is None
        assert storage.photo_url("") is None


class TestRoster:
    def test_the_team_list_sends_a_url_and_not_a_path(self):
        request = factory.get("/team/")
        force_authenticate(request, user=EMPLOYEE)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence, patch(
            "apps.b2b.workspace.storage.default_storage"
        ) as store:
            repo.list_team.return_value = [
                {
                    "id": 8,
                    "full_name": "Sardor Azimov",
                    "role": "employee",
                    "photo": "b2b/workspace/55/avatar/sardor.jpg",
                },
            ]
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            store.url.return_value = "https://cdn/media/sardor.jpg"
            response = WorkspaceTeamView.as_view()(request)

        assert response.status_code == 200
        assert response.data[0]["photo"] == "https://cdn/media/sardor.jpg"

    def test_somebody_with_no_photo_is_sent_as_having_none(self):
        request = factory.get("/team/")
        force_authenticate(request, user=EMPLOYEE)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence:
            repo.list_team.return_value = [
                {"id": 8, "full_name": "Sardor Azimov", "role": "employee", "photo": None},
            ]
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceTeamView.as_view()(request)

        assert response.data[0]["photo"] is None
