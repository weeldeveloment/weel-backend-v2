from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from rest_framework.test import APIClient
from rest_framework import status

from admin_auth.authentication import create_admin_tokens
from users.models.partners import Partner


User = get_user_model()


class ChatPushNotificationTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.admin = User.objects.create(username="admin", is_staff=True, is_active=True)
        self.partner = Partner.objects.create(
            first_name="Test",
            last_name="Partner",
            username="partner1",
            phone_number="+998901234568",
            is_active=True,
        )
        tokens = create_admin_tokens(self.admin)
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    @patch("chat.views.NotificationService.send_to_partner")
    def test_admin_send_triggers_partner_push(self, mock_send_to_partner):
        response = self.api.post(
            "/api/chat/send/",
            data={"receiver_id": self.partner.id, "content": "Hello there"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_send_to_partner.assert_called_once()
