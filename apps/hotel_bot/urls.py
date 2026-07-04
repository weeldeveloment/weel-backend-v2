from django.urls import path

from .webhook import HotelBotWebhookView

urlpatterns = [
    path("webhook/<str:secret_token>/", HotelBotWebhookView.as_view(), name="hotel-bot-webhook"),
]
