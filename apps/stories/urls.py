from django.urls import path
from rest_framework.routers import DefaultRouter

from .admin_views import AdminStoryDeleteView, AdminStoryListView, AdminStoryModerateView
from .views import (
    AdminBannerCreateView,
    AdminBannerDeleteView,
    AdminBannerDetailView,
    AdminBannerListView,
    AdminBannerUpdateView,
    AdminNewsCreateView,
    AdminNewsDeleteView,
    AdminNewsDetailView,
    AdminNewsListView,
    AdminNewsUpdateView,
    StoryViewSet,
    PartnerStoryListView,
    PublicStoryListView,
)

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
    # Admin moderation endpoints
    path(
        "admin/stories/",
        AdminStoryListView.as_view(),
        name="admin-story-list",
    ),
    path(
        "admin/stories/<uuid:story_guid>/moderate/",
        AdminStoryModerateView.as_view(),
        name="admin-story-moderate",
    ),
    path(
        "admin/stories/<uuid:story_guid>/delete/",
        AdminStoryDeleteView.as_view(),
        name="admin-story-delete",
    ),
    # Admin platform news endpoints
    path(
        "admin/news/",
        AdminNewsListView.as_view(),
        name="admin-news-list",
    ),
    path(
        "admin/news/create/",
        AdminNewsCreateView.as_view(),
        name="admin-news-create",
    ),
    path(
        "admin/news/<uuid:news_guid>/",
        AdminNewsDetailView.as_view(),
        name="admin-news-detail",
    ),
    path(
        "admin/news/<uuid:news_guid>/update/",
        AdminNewsUpdateView.as_view(),
        name="admin-news-update",
    ),
    path(
        "admin/news/<uuid:news_guid>/delete/",
        AdminNewsDeleteView.as_view(),
        name="admin-news-delete",
    ),
    # Admin banner endpoints
    path(
        "admin/banners/",
        AdminBannerListView.as_view(),
        name="admin-banner-list",
    ),
    path(
        "admin/banners/create/",
        AdminBannerCreateView.as_view(),
        name="admin-banner-create",
    ),
    path(
        "admin/banners/<uuid:banner_guid>/",
        AdminBannerDetailView.as_view(),
        name="admin-banner-detail",
    ),
    path(
        "admin/banners/<uuid:banner_guid>/update/",
        AdminBannerUpdateView.as_view(),
        name="admin-banner-update",
    ),
    path(
        "admin/banners/<uuid:banner_guid>/delete/",
        AdminBannerDeleteView.as_view(),
        name="admin-banner-delete",
    ),
]
