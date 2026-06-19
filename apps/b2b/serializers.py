from __future__ import annotations

from decimal import Decimal
from rest_framework import serializers


class B2BCompanySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=200)
    legal_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    inn = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    legal_address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    industry = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    employee_count = serializers.IntegerField(required=False, allow_null=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class B2BUserSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    company_id = serializers.IntegerField(read_only=True)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    first_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    last_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    role = serializers.ChoiceField(choices=["owner", "performer"], required=False, default="performer")
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class B2BDepartmentSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    company_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=100)
    created_at = serializers.DateTimeField(read_only=True)


class B2BEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    company_id = serializers.IntegerField(read_only=True)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    department_name = serializers.CharField(read_only=True, allow_null=True)
    full_name = serializers.CharField(max_length=200)
    position = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    passport_series = serializers.CharField(max_length=10, required=False, allow_blank=True, allow_null=True)
    passport_number = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    pinfl = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    individual_limit = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    status = serializers.ChoiceField(choices=["available", "on_trip", "blocked"], required=False, default="available")
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class BusinessTripSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    company_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=200)
    destination_city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)
    budget = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    status = serializers.ChoiceField(
        choices=["draft", "pending", "active", "completed", "cancelled"],
        required=False,
        default="draft",
    )
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    created_by = serializers.IntegerField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class TripEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField(read_only=True)
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField(read_only=True, allow_null=True)
    position = serializers.CharField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    email = serializers.CharField(read_only=True, allow_null=True)
    property_id = serializers.IntegerField(required=False, allow_null=True)
    room_id = serializers.IntegerField(required=False, allow_null=True)
    check_in = serializers.DateField(required=False, allow_null=True)
    check_out = serializers.DateField(required=False, allow_null=True)
    pms_booking_id = serializers.IntegerField(required=False, allow_null=True)
    status = serializers.ChoiceField(
        choices=["invited", "confirmed", "checked_in", "checked_out", "cancelled"],
        required=False,
        default="invited",
    )
    created_at = serializers.DateTimeField(read_only=True)


class TravelPolicySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    company_id = serializers.IntegerField(read_only=True)
    budget_per_trip = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    monthly_budget = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    allowed_star_ratings = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    allowed_weel_classifications = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    blacklisted_properties = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    preferred_properties = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    updated_at = serializers.DateTimeField(read_only=True)


class BudgetRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField()
    employee_id = serializers.IntegerField()
    trip_name = serializers.CharField(read_only=True, allow_null=True)
    employee_name = serializers.CharField(read_only=True, allow_null=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    reason = serializers.CharField(max_length=500)
    status = serializers.CharField(read_only=True)
    reviewed_by = serializers.IntegerField(read_only=True, allow_null=True)
    reviewed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class ReviewBudgetRequestSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["approved", "rejected"])


class TravelVoucherSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField(read_only=True)
    voucher_number = serializers.CharField(read_only=True)
    pdf_url = serializers.CharField(read_only=True, allow_null=True)
    generated_at = serializers.DateTimeField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
