from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import StoryViewSet, PartnerStoryListView, PublicStoryListView

router = DefaultRouter()
router.register(r"stories", StoryViewSet, basename="stories"),

urlpatterns = [
    *router.urls,
    path(
        "stories/<uuid:story_id>/<uuid:media_id>/",
        StoryViewSet.as_view(
            {
                "get": "retrieve_media",
                "delete": "destroy_media",
            }
        ),
        name="story-media-retrieve-detail",
    ),
    path(
        "partner/stories/",
        PartnerStoryListView.as_view(),
        name="partner-story-list",
    ),
    path(
        "public/stories/",
        PublicStoryListView.as_view(),
        name="public-story-list",
    ),
]
