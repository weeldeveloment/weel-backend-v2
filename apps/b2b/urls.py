from django.urls import path
from apps.b2b.views import (
    B2BCompanyView,
    B2BDepartmentListCreateView,
    B2BEmployeeListCreateView,
    B2BEmployeeRetrieveUpdateView,
    B2BStatisticsView,
    BusinessTripListCreateView,
    BusinessTripRetrieveUpdateView,
    TripEmployeeListCreateView,
    TravelPolicyView,
    BudgetRequestListCreateView,
    BudgetRequestReviewView,
    TripVoucherView,
)

urlpatterns = [
    path("company/", B2BCompanyView.as_view(), name="b2b-company"),
    path("departments/", B2BDepartmentListCreateView.as_view(), name="b2b-departments"),
    path("employees/", B2BEmployeeListCreateView.as_view(), name="b2b-employees"),
    path("employees/<int:employee_id>/", B2BEmployeeRetrieveUpdateView.as_view(), name="b2b-employee-detail"),
    path("trips/", BusinessTripListCreateView.as_view(), name="b2b-trips"),
    path("trips/<int:trip_id>/", BusinessTripRetrieveUpdateView.as_view(), name="b2b-trip-detail"),
    path("trips/<int:trip_id>/employees/", TripEmployeeListCreateView.as_view(), name="b2b-trip-employees"),
    path("trips/<int:trip_id>/voucher/", TripVoucherView.as_view(), name="b2b-trip-voucher"),
    path("travel-policy/", TravelPolicyView.as_view(), name="b2b-travel-policy"),
    path("budget-requests/", BudgetRequestListCreateView.as_view(), name="b2b-budget-requests"),
    path("budget-requests/<int:request_id>/review/", BudgetRequestReviewView.as_view(), name="b2b-budget-request-review"),
    path("statistics/", B2BStatisticsView.as_view(), name="b2b-statistics"),
]
