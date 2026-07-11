from django.urls import path
from apps.hotels.views import (
    HotelSearchView,
    HotelDetailView,
    HotelRoomSelectView,
    HotelRoomPriceView,
    HotelReviewsView,
)

urlpatterns = [
    path("search/", HotelSearchView.as_view(), name="hotel-search"),
    path("<str:guid>/rooms/", HotelRoomSelectView.as_view(), name="hotel-room-select"),
    path("<str:guid>/rooms/<int:room_id>/price/", HotelRoomPriceView.as_view(), name="hotel-room-price"),
    path("<str:guid>/reviews/", HotelReviewsView.as_view(), name="hotel-reviews"),
    path("<str:guid>/", HotelDetailView.as_view(), name="hotel-detail"),
]
