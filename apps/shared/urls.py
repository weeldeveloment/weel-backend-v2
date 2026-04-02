from django.urls import path
from apps.shared import views

urlpatterns = [
    path("frontend/", views.FrontendLogView.as_view(), name="frontend-log"),
]
