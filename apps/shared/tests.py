"""
Tests for the shared app: date helpers, utility regex, utils (ErrorsFormatter, normalizers),
permissions, FrontendLogView, compress_image (mocked), compress_video (mocked), URL.
"""

import logging
import re
from datetime import date
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError as DRFValidationError

from shared.date import month_start, month_end, parse_yyyy_mm_dd
from shared.utility import (
    USERNAME_REGEX,
    USER_NAME_REGEX,
    PHONE_NUMBER_REGEX,
    PASSWORD_REGEX,
)
from shared.utils import (
    ErrorsFormatter,
    _single_error_response,
    _normalize_plum_like_errors,
    _format_token_error_response,
)
from shared.permissions import (
    IsPartner,
    IsClient,
    IsClientOrPartner,
    IsPartnerOwnerProperty,
)
from shared.views import FrontendLogView

logging.getLogger("django.request").setLevel(logging.ERROR)
logging.getLogger("frontend").setLevel(logging.ERROR)


# ──────────────────────────────────────────────
# 1. date.py
# ──────────────────────────────────────────────


class DateHelpersTests(TestCase):
    def test_month_start(self):
        self.assertEqual(month_start(date(2026, 2, 15)), date(2026, 2, 1))
        self.assertEqual(month_start(date(2026, 12, 31)), date(2026, 12, 1))

    def test_month_end(self):
        self.assertEqual(month_end(date(2026, 2, 1)), date(2026, 2, 28))
        self.assertEqual(month_end(date(2024, 2, 1)), date(2024, 2, 29))
        self.assertEqual(month_end(date(2026, 4, 15)), date(2026, 4, 30))

    def test_parse_yyyy_mm_dd_valid(self):
        self.assertEqual(parse_yyyy_mm_dd("2026-02-25", "date"), date(2026, 2, 25))

    def test_parse_yyyy_mm_dd_invalid_raises(self):
        with self.assertRaises(DRFValidationError):
            parse_yyyy_mm_dd("not-a-date", "date")


# ──────────────────────────────────────────────
# 2. utility.py (regex)
# ──────────────────────────────────────────────


class UtilityRegexTests(TestCase):
    def test_username_regex(self):
        self.assertTrue(re.match(USERNAME_REGEX, "user_123"))
        self.assertTrue(re.match(USERNAME_REGEX, "Abc"))
        self.assertFalse(re.match(USERNAME_REGEX, "ab"))  # too short
        self.assertFalse(re.match(USERNAME_REGEX, "user-name"))  # hyphen

    def test_phone_number_regex(self):
        self.assertTrue(re.match(PHONE_NUMBER_REGEX, "+998901234567"))
        self.assertTrue(re.match(PHONE_NUMBER_REGEX, "998901234567"))
        self.assertFalse(re.match(PHONE_NUMBER_REGEX, "+79991234567"))

    def test_password_regex(self):
        self.assertTrue(re.match(PASSWORD_REGEX, "Pass123!"))
        self.assertFalse(re.match(PASSWORD_REGEX, "nouppercase1!"))
        self.assertFalse(re.match(PASSWORD_REGEX, "NOLOWER1!"))


# ──────────────────────────────────────────────
# 3. utils.py (ErrorsFormatter, normalizers)
# ──────────────────────────────────────────────


class ErrorsFormatterTests(TestCase):
    def test_simple_message(self):
        formatter = ErrorsFormatter(ValueError("Bad request"), 400)
        result = formatter()
        self.assertIn("errors", result)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["detail"], "Bad request")
        self.assertEqual(result["errors"][0]["status_code"], 400)

    def test_drf_style_error_detail(self):
        err = DRFValidationError({"name": ["This field is required."]})
        err.get_full_details = lambda: {"name": [{"message": "This field is required.", "code": "required"}]}
        formatter = ErrorsFormatter(err, 400)
        result = formatter()
        self.assertIn("errors", result)
        self.assertTrue(any(e.get("field") == "name" for e in result["errors"]))


class SingleErrorResponseTests(TestCase):
    def test_single_error_response(self):
        errors = [{"detail": "x", "status_code": 422}]
        out = _single_error_response(errors, "Single message")
        self.assertEqual(len(out["errors"]), 1)
        self.assertEqual(out["errors"][0]["detail"], "Single message")
        self.assertEqual(out["errors"][0]["status_code"], 422)


class NormalizePlumLikeErrorsTests(TestCase):
    def test_returns_data_unchanged_when_single_error(self):
        data = {"errors": [{"detail": "One", "status_code": 400}]}
        self.assertEqual(_normalize_plum_like_errors(data), data)

    def test_plum_error_message_message_collapsed(self):
        data = {
            "errors": [
                {"field": "errorMessage.message", "detail": "Карты не принимаются", "status_code": 400},
                {"field": "other", "detail": "x", "status_code": 400},
            ]
        }
        out = _normalize_plum_like_errors(data)
        self.assertEqual(len(out["errors"]), 1)
        self.assertEqual(out["errors"][0]["detail"], "Карты не принимаются")


class FormatTokenErrorResponseTests(TestCase):
    def test_format_token_error_response(self):
        class Res:
            data = {"detail": "Token is invalid"}
            status_code = 401
        out = _format_token_error_response(Res())
        self.assertIn("errors", out)
        self.assertIn("hint", out["errors"][0])


# ──────────────────────────────────────────────
# 4. permissions
# ──────────────────────────────────────────────


class IsPartnerPermissionTests(TestCase):
    def test_anonymous_false(self):
        req = MagicMock(user=None)
        self.assertFalse(IsPartner().has_permission(req, None))

    def test_client_false(self):
        from users.models.clients import Client
        req = MagicMock(user=MagicMock(spec=Client, is_active=True))
        req.user.__class__ = Client
        self.assertFalse(IsPartner().has_permission(req, None))

    def test_partner_active_true(self):
        from users.models.partners import Partner
        partner = MagicMock(spec=Partner, is_active=True)
        partner.__class__ = Partner
        req = MagicMock(user=partner)
        self.assertTrue(IsPartner().has_permission(req, None))


class IsClientPermissionTests(TestCase):
    def test_anonymous_false(self):
        req = MagicMock(user=None)
        self.assertFalse(IsClient().has_permission(req, None))

    def test_client_active_true(self):
        from users.models.clients import Client
        client = MagicMock(spec=Client, is_active=True)
        client.__class__ = Client
        req = MagicMock(user=client)
        self.assertTrue(IsClient().has_permission(req, None))


class IsClientOrPartnerPermissionTests(TestCase):
    def test_anonymous_false(self):
        req = MagicMock(user=None)
        self.assertFalse(IsClientOrPartner().has_permission(req, None))

    def test_client_true(self):
        from users.models.clients import Client
        client = MagicMock(spec=Client, is_active=True)
        client.__class__ = Client
        req = MagicMock(user=client)
        self.assertTrue(IsClientOrPartner().has_permission(req, None))

    def test_partner_true(self):
        from users.models.partners import Partner
        partner = MagicMock(spec=Partner, is_active=True)
        partner.__class__ = Partner
        req = MagicMock(user=partner)
        self.assertTrue(IsClientOrPartner().has_permission(req, None))


class IsPartnerOwnerPropertyPermissionTests(TestCase):
    def test_safe_method_allowed(self):
        from users.models.partners import Partner
        partner = MagicMock(spec=Partner, is_active=True)
        partner.__class__ = Partner
        req = MagicMock(user=partner, method="GET")
        obj = MagicMock(partner=partner)
        perm = IsPartnerOwnerProperty()
        self.assertTrue(perm.has_object_permission(req, None, obj))

    def test_owner_can_edit(self):
        from users.models.partners import Partner
        partner = MagicMock(spec=Partner, is_active=True)
        partner.__class__ = Partner
        req = MagicMock(user=partner, method="PATCH")
        obj = MagicMock(partner=partner)
        perm = IsPartnerOwnerProperty()
        self.assertTrue(perm.has_object_permission(req, None, obj))

    def test_non_owner_cannot_edit(self):
        from users.models.partners import Partner
        owner = MagicMock()
        other = MagicMock()
        req = MagicMock(user=other, method="PUT")
        obj = MagicMock(partner=owner)
        perm = IsPartnerOwnerProperty()
        self.assertFalse(perm.has_object_permission(req, None, obj))


# ──────────────────────────────────────────────
# 5. FrontendLogView
# ──────────────────────────────────────────────


class FrontendLogViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_post_returns_201_with_level_and_message(self):
        response = self.client.post(
            "/api/logs/frontend/",
            data={"level": "info", "message": "Test message"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get("ok"), True)

    def test_post_defaults_level_and_message(self):
        response = self.client.post("/api/logs/frontend/", data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


# ──────────────────────────────────────────────
# 6. compress_image (mocked)
# ──────────────────────────────────────────────


class CompressImageTests(TestCase):
    @patch("shared.compress_image.settings")
    @patch("shared.compress_image.Image.open")
    def test_utils_image_invalid_extension_raises(self, mock_open, mock_settings):
        from shared.compress_image import utils_image
        from django.core.files.uploadedfile import SimpleUploadedFile
        mock_settings.ALLOWED_PHOTO_EXTENSION = ["jpg", "jpeg", "png"]
        f = SimpleUploadedFile("x.pdf", b"fake")
        with self.assertRaises(ValueError):
            utils_image(f)

    @patch("shared.compress_image.settings")
    def test_compress_image_returns_file(self, mock_settings):
        from shared.compress_image import compress_image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io
        mock_settings.ALLOWED_PHOTO_EXTENSION = ["jpg", "jpeg", "png"]
        buf = io.BytesIO()
        Image.new("RGB", (100, 100), color="red").save(buf, format="JPEG")
        buf.seek(0)
        f = SimpleUploadedFile("test.jpg", buf.read(), content_type="image/jpeg")
        out = compress_image(f, 640, quality=85)
        self.assertIsNotNone(out)
        self.assertTrue(hasattr(out, "name"))


# ──────────────────────────────────────────────
# 7. compress_video (mocked subprocess)
# ──────────────────────────────────────────────


class CompressVideoTests(TestCase):
    def test_compress_video_invalid_extension_raises(self):
        from shared.compress_video import compress_video
        from django.core.files.uploadedfile import SimpleUploadedFile
        from unittest.mock import patch
        with patch("shared.compress_video.settings") as mock_settings:
            mock_settings.ALLOWED_VIDEO_EXTENSION = ["mp4", "mov"]
            f = SimpleUploadedFile("x.xyz", b"fake")
            with self.assertRaises(ValueError):
                compress_video(f)

    @patch("shared.compress_video.subprocess.run")
    def test_get_optimal_resolution_returns_720_for_720p(self, mock_run):
        from shared.compress_video import get_optimal_resolution
        import json
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "streams": [{"codec_type": "video", "width": 1280, "height": 720}]
            }),
        )
        f = MagicMock()
        f.temporary_file_path = MagicMock(return_value="/tmp/x.mp4")
        self.assertEqual(get_optimal_resolution(f), "720")


# ──────────────────────────────────────────────
# 8. URL
# ──────────────────────────────────────────────


class SharedURLTests(TestCase):
    def test_frontend_log_url_resolves(self):
        url = reverse("shared:frontend-log")
        self.assertIn("frontend", url)
