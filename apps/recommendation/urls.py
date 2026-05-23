from django.urls import path

from .views import PersonalizedRecommendationsView

urlpatterns = [
    path(
        "recommendations/personal/",
        PersonalizedRecommendationsView.as_view(),
        name="personalized-recommendations",
    ),
]
