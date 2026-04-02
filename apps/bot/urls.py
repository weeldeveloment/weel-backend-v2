from django.urls import path

from .webhook import TelegramWebhookView

urlpatterns = [
    path("webhook/<str:secret_token>/", TelegramWebhookView.as_view(), name="bot-webhook"),
]
