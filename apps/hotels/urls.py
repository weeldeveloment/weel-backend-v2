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
    path("<int:hotel_id>/", HotelDetailView.as_view(), name="hotel-detail"),
    path("<int:hotel_id>/rooms/", HotelRoomSelectView.as_view(), name="hotel-room-select"),
    path("<int:hotel_id>/rooms/<int:room_id>/price/", HotelRoomPriceView.as_view(), name="hotel-room-price"),
    path("<int:hotel_id>/reviews/", HotelReviewsView.as_view(), name="hotel-reviews"),
]
