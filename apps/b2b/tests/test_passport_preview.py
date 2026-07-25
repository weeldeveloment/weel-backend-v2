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
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.passport_ocr import PassportOCRError
from apps.b2b.views import B2BEmployeePassportPreviewView


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


@patch("apps.b2b.views.default_storage")
@patch("apps.b2b.views.extract_passport_data")
def test_passport_preview_success_does_not_persist(mock_extract, mock_storage):
    mock_extract.return_value = {
        "full_name": "Иванов Иван",
        "date_of_birth": date(1990, 1, 1),
        "passport_series": "AA1234567",
        "passport_pinfl": "12345678901234",
    }
    response = _post(_files())
    assert response.status_code == 200
    assert response.data == mock_extract.return_value
    mock_storage.save.assert_not_called()


@patch("apps.b2b.views.extract_passport_data")
def test_passport_preview_ocr_error_returns_400(mock_extract):
    mock_extract.side_effect = PassportOCRError("shablon topilmadi")
    response = _post(_files())
    assert response.status_code == 400
    assert response.data == {"detail": "shablon topilmadi"}


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
