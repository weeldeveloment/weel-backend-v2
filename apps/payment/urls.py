from django.urls import path

from . import views

urlpatterns = [
    path("exchange-rate/", views.ExchangeRateView.as_view(), name="exchange-rate"),
]
