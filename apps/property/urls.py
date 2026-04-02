from django.urls import path

from .views import (
    PropertyTypeListView,
    PropertyListCreateView,
    PropertyFilterByLinkView,
    ApartmentPropertyListCreateView,
    CottagePropertyListCreateView,
    RegionPropertyListView,
    PropertyImageCreateView,
    PropertyServiceListView,
    PropertyReviewListCreateView,
    PropertyImageUpdateDeleteView,
    PropertyRetrieveUpdateDestroyView,
    PartnerPropertyReviewListView,
    PartnerPropertyListView,
    SavedPropertyListView,
    PropertyFavoriteToggleView,
    RegionListView,
    DistrictListView,
    LocationListView,
    UnifiedRecommendationsListView,
    CategoryListView,
    CategoryPropertyRecommendationView,
    CategoryLatestPropertyListView,
)


urlpatterns = [
    path("types/", PropertyTypeListView.as_view(), name="property-type-list"),
    path("location/", LocationListView.as_view(), name="location"),
    path("regions/", RegionListView.as_view(), name="region-list"),
    path(
        "regions/<uuid:region_id>/properties/",
        RegionPropertyListView.as_view(),
        name="region-property-list",
    ),
    path("districts/", DistrictListView.as_view(), name="district-list"),
    path("services/", PropertyServiceListView.as_view(), name="property-service-list"),
    path(
        "recommendations/",
        UnifiedRecommendationsListView.as_view(),
        name="property-recommendations",
    ),
    path("categories/", CategoryListView.as_view(), name="category-list"),
    path(
        "categories/<uuid:category_id>/properties/latest/",
        CategoryLatestPropertyListView.as_view(),
        name="category-latest-properties",
    ),
    path(
        "categories/<uuid:category_id>/properties/",
        CategoryPropertyRecommendationView.as_view(),
        name="category-property-recommendation",
    ),
    path("properties/", PropertyListCreateView.as_view(), name="property-list-create"),
    path(
        "properties/apartments/",
        ApartmentPropertyListCreateView.as_view(),
        name="apartment-property-list-create",
    ),
    path(
        "properties/cottages/",
        CottagePropertyListCreateView.as_view(),
        name="cottage-property-list-create",
    ),
    path(
        "properties/favorites/",
        SavedPropertyListView.as_view(),
        name="saved-property-list",
    ),
    path(
        "properties/filter-by-link/",
        PropertyFilterByLinkView.as_view(),
        name="property-filter-by-link",
    ),
    path("partner/properties/", PartnerPropertyListView.as_view(), name="partner-property-list"),
    path(
        "properties/<uuid:property_id>/",
        PropertyRetrieveUpdateDestroyView.as_view(),
        name="property-retrieve-update-destroy",
    ),
    path(
        "properties/<uuid:property_id>/favorite/",
        PropertyFavoriteToggleView.as_view(),
        name="property-favorite-toggle",
    ),
    path(
        "properties/<uuid:property_id>/images/",
        PropertyImageCreateView.as_view(),
        name="property-image-create",
    ),
    path(
        "properties/<uuid:property_id>/images/<uuid:image_id>/",
        PropertyImageUpdateDeleteView.as_view(),
        name="property-image-update-delete",
    ),
    path(
        "properties/<uuid:property_id>/reviews/",
        PropertyReviewListCreateView.as_view(),
        name="property-review-list-create",
    ),
    path(
        "properties/<uuid:property_id>/partner/reviews/",
        PartnerPropertyReviewListView.as_view(),
        name="partner-property-reviews",
    ),
]
