from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(
        USE_TZ=True,
        TIME_ZONE="UTC",
        REST_FRAMEWORK={},
    )

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.passport_ocr import PassportOCRError
from apps.b2b.views import B2BEmployeePassportPreviewStatusView, B2BEmployeePassportPreviewView


def _authenticated_user():
    return SimpleNamespace(id=9, company_id=55, role="owner", is_authenticated=True)


def _files():
    return {
        "passport_upload_front": SimpleUploadedFile("front.jpg", b"front-bytes", content_type="image/jpeg"),
        "passport_upload_back": SimpleUploadedFile("back.jpg", b"back-bytes", content_type="image/jpeg"),
    }


def _post(files):
    request = APIRequestFactory().post("/api/b2b/employees/passport-preview/", files, format="multipart")
    force_authenticate(request, user=_authenticated_user())
    return B2BEmployeePassportPreviewView.as_view()(request)


def _get_status(job_id):
    request = APIRequestFactory().get(f"/api/b2b/employees/passport-preview/{job_id}/")
    force_authenticate(request, user=_authenticated_user())
    return B2BEmployeePassportPreviewStatusView.as_view()(request, job_id=job_id)


# The view saves the uploaded images and hands off to a Celery task
# (apps.b2b.tasks.run_passport_ocr_job) instead of running OCR inline —
# CELERY_TASK_ALWAYS_EAGER runs that task synchronously so these tests don't
# need a real worker/broker.
@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
@patch("apps.b2b.passport_ocr.extract_passport_data")
def test_passport_preview_success_reports_done_via_status_poll(mock_extract):
    mock_extract.return_value = {
        "full_name": "Иванов Иван",
        "date_of_birth": date(1990, 1, 1),
        "passport_series": "AA1234567",
        "passport_pinfl": "12345678901234",
    }
    response = _post(_files())
    assert response.status_code == 202
    job_id = response.data["job_id"]
    assert job_id

    status_response = _get_status(job_id)
    assert status_response.status_code == 200
    assert status_response.data["status"] == "done"
    assert status_response.data["ocr_token"] == job_id
    for key, value in mock_extract.return_value.items():
        assert status_response.data[key] == value


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
@patch("apps.b2b.passport_ocr.extract_passport_data")
def test_passport_preview_ocr_error_reports_error_via_status_poll(mock_extract):
    mock_extract.side_effect = PassportOCRError("shablon topilmadi")
    response = _post(_files())
    assert response.status_code == 202
    job_id = response.data["job_id"]

    status_response = _get_status(job_id)
    assert status_response.status_code == 200
    assert status_response.data == {"status": "error", "detail": "shablon topilmadi"}


def test_passport_preview_status_unknown_job_returns_404():
    response = _get_status("does-not-exist")
    assert response.status_code == 404


def test_passport_preview_requires_auth():
    request = APIRequestFactory().post("/api/b2b/employees/passport-preview/", _files(), format="multipart")
    response = B2BEmployeePassportPreviewView.as_view()(request)
    assert response.status_code == 401


def test_passport_preview_missing_file_returns_400():
    request = APIRequestFactory().post(
        "/api/b2b/employees/passport-preview/",
        {"passport_upload_front": _files()["passport_upload_front"]},
        format="multipart",
    )
    force_authenticate(request, user=_authenticated_user())
    response = B2BEmployeePassportPreviewView.as_view()(request)
    assert response.status_code == 400
