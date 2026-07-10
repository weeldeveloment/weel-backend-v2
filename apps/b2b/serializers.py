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
    """Xodimni ko'rsatish (GET) hamda yangilash (PATCH, partial) uchun serializer.

    ``passport_upload`` bu yerda faqat o'qish uchun (saqlangan fayl URL manzili) —
    xodim yaratishda fayl ``B2BEmployeeCreateSerializer`` orqali qabul qilinadi va
    view darajasida ``default_storage`` ga yuklanadi.
    """
    id = serializers.IntegerField(read_only=True)
    company_id = serializers.IntegerField(read_only=True)
    department_id = serializers.IntegerField(required=True)
    department_name = serializers.CharField(read_only=True, allow_null=True)
    full_name = serializers.CharField(max_length=200)
    position = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(required=True)
    phone = serializers.CharField(max_length=20, required=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    passport_series = serializers.CharField(max_length=10, required=False, allow_blank=True, allow_null=True)
    passport_number = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    passport_upload = serializers.CharField(read_only=True, allow_null=True)
    pinfl = serializers.CharField(max_length=20, required=True)
    individual_limit = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    status = serializers.ChoiceField(choices=["available", "on_trip", "blocked"], required=False, default="available")
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class B2BEmployeeCreateSerializer(B2BEmployeeSerializer):
    """POST ``/b2b/employees/`` uchun serializer (multipart/form-data).

    ``pinfl``, ``email``, ``phone``, ``department_id`` — xodim yaratishning
    asosiy majburiy maydonlari. ``passport_upload`` fayl sifatida shu yerda
    validatsiya qilinadi (view uni ``request.FILES`` orqali o'qiydi, chunki
    u B2BEmployeeSerializer'da faqat o'qish uchun CharField).
    """
    passport_upload = serializers.FileField(required=True)

    def validate_passport_upload(self, file):
        max_size = 5 * 1024 * 1024  # 5MB
        if file.size > max_size:
            raise serializers.ValidationError("Fayl hajmi 5MB dan oshmasligi kerak.")
        return file


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


class HotelBookingRoomRequestSerializer(serializers.Serializer):
    """One room in a booking request — 1 or 2 employees per room."""
    room_id = serializers.IntegerField()
    employee_ids = serializers.ListField(
        child=serializers.IntegerField(), min_length=1, max_length=2,
    )


class HotelBookingRequestCreateSerializer(serializers.Serializer):
    """POST body for 'bosqich 2': hotel + dates already chosen in bosqich 1,
    now pick rooms and assign employees, then submit as one booking."""
    trip_id = serializers.IntegerField()
    hotel_guid = serializers.CharField()
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    rooms = HotelBookingRoomRequestSerializer(many=True)

    def validate(self, data):
        if data["check_out"] <= data["check_in"]:
            raise serializers.ValidationError({"check_out": "check_out must be after check_in."})
        rooms = data.get("rooms") or []
        if not rooms:
            raise serializers.ValidationError({"rooms": "At least one room is required."})
        seen_rooms: set[int] = set()
        seen_employees: set[int] = set()
        for room in rooms:
            if room["room_id"] in seen_rooms:
                raise serializers.ValidationError({"rooms": "Duplicate room_id in request."})
            seen_rooms.add(room["room_id"])
            for employee_id in room["employee_ids"]:
                if employee_id in seen_employees:
                    raise serializers.ValidationError(
                        {"rooms": "An employee cannot be assigned to more than one room."}
                    )
                seen_employees.add(employee_id)
        return data


class HotelBookingRoomEmployeeSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    full_name = serializers.CharField(allow_null=True)
    position = serializers.CharField(allow_null=True)


class HotelBookingRoomSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    room_id = serializers.IntegerField()
    room_name = serializers.CharField(allow_null=True)
    price_per_night = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    pms_booking_id = serializers.IntegerField(allow_null=True)
    employees = HotelBookingRoomEmployeeSerializer(many=True)


class HotelBookingRequestSerializer(serializers.Serializer):
    """Summary row — used in the booking history LIST (shows as ONE entry)."""
    id = serializers.IntegerField(read_only=True)
    company_id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField(allow_null=True)
    hotel_guid = serializers.CharField()
    hotel_name = serializers.CharField(allow_null=True)
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    status = serializers.ChoiceField(
        choices=["pending", "confirmed", "rejected"], read_only=True,
    )
    room_count = serializers.IntegerField(default=0)
    employee_count = serializers.IntegerField(default=0)
    requested_by = serializers.IntegerField(read_only=True, allow_null=True)
    reviewed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class HotelBookingRequestDetailSerializer(HotelBookingRequestSerializer):
    """Full detail — used when clicking into one booking ("hammasi ko'rinadi")."""
    rooms = HotelBookingRoomSerializer(many=True)


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
    """Byudjet so'rovi: aynan bittasi berilishi shart — ``employee_id``
    (bitta xodim uchun) yoki ``department_id`` (butun bo'lim uchun).
    ``trip_id`` ixtiyoriy — mavjud bo'lsa, so'rov shu komandirovka bilan
    bog'lanadi."""
    id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField(required=False, allow_null=True)
    employee_id = serializers.IntegerField(required=False, allow_null=True)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    trip_name = serializers.CharField(read_only=True, allow_null=True)
    employee_name = serializers.CharField(read_only=True, allow_null=True)
    department_name = serializers.CharField(read_only=True, allow_null=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, allow_null=True)
    status = serializers.CharField(read_only=True)
    reviewed_by = serializers.IntegerField(read_only=True, allow_null=True)
    reviewed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    review_description = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)

    def validate(self, data):
        employee_id = data.get("employee_id")
        department_id = data.get("department_id")
        if bool(employee_id) == bool(department_id):
            raise serializers.ValidationError(
                "Exactly one of employee_id or department_id is required."
            )
        return data


class ReviewBudgetRequestSerializer(serializers.Serializer):
    """Owner tomonidan byudjet so'rovini tasdiqlash/rad etish. `description`
    — owner nega tasdiqlagani/rad etganini yozadigan ixtiyoriy izoh."""
    status = serializers.ChoiceField(choices=["approved", "rejected"])
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, allow_null=True)


class TravelVoucherSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField(read_only=True)
    voucher_number = serializers.CharField(read_only=True)
    pdf_url = serializers.CharField(read_only=True, allow_null=True)
    generated_at = serializers.DateTimeField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class ActiveTripEmployeeSerializer(serializers.Serializer):
    """Serializer for the "xodimlar yolda yoki borgan" endpoint.

    Each row represents a single ``b2b_trip_employee`` assignment together
    with the joined employee, trip and department context that the frontend
    needs to render a single card / row.
    """
    trip_employee_id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField(read_only=True)
    employee_id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True, allow_null=True)
    position = serializers.CharField(read_only=True, allow_null=True)
    email = serializers.CharField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    department_id = serializers.IntegerField(read_only=True, allow_null=True)
    department_name = serializers.CharField(read_only=True, allow_null=True)
    trip_name = serializers.CharField(read_only=True, allow_null=True)
    destination_city = serializers.CharField(read_only=True, allow_null=True)
    trip_start_date = serializers.DateField(read_only=True, allow_null=True)
    trip_end_date = serializers.DateField(read_only=True, allow_null=True)
    check_in = serializers.DateField(read_only=True, allow_null=True)
    check_out = serializers.DateField(read_only=True, allow_null=True)
    trip_status = serializers.CharField(read_only=True)
    trip_employee_status = serializers.CharField(read_only=True)
    assigned_at = serializers.DateTimeField(read_only=True)


class RecentTripEmployeeSerializer(serializers.Serializer):
    """Serializer for the "last N employees on a business trip" endpoint."""
    trip_employee_id = serializers.IntegerField(read_only=True)
    trip_id = serializers.IntegerField(read_only=True)
    employee_id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True, allow_null=True)
    position = serializers.CharField(read_only=True, allow_null=True)
    email = serializers.CharField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    department_id = serializers.IntegerField(read_only=True, allow_null=True)
    department_name = serializers.CharField(read_only=True, allow_null=True)
    trip_name = serializers.CharField(read_only=True, allow_null=True)
    destination_city = serializers.CharField(read_only=True, allow_null=True)
    trip_start_date = serializers.DateField(read_only=True, allow_null=True)
    trip_end_date = serializers.DateField(read_only=True, allow_null=True)
    check_in = serializers.DateField(read_only=True, allow_null=True)
    check_out = serializers.DateField(read_only=True, allow_null=True)
    trip_status = serializers.CharField(read_only=True)
    trip_employee_status = serializers.CharField(read_only=True)
    assigned_at = serializers.DateTimeField(read_only=True)


class TopEmployeeByTripsSerializer(serializers.Serializer):
    """Serializer for the "top N employees by trip count" endpoint."""
    employee_id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True, allow_null=True)
    position = serializers.CharField(read_only=True, allow_null=True)
    email = serializers.CharField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    department_id = serializers.IntegerField(read_only=True, allow_null=True)
    department_name = serializers.CharField(read_only=True, allow_null=True)
    trip_count = serializers.IntegerField(read_only=True)


class TopHotelByBookingsSerializer(serializers.Serializer):
    """Serializer for the "top N hotels by booking count" endpoint."""
    hotel_guid = serializers.CharField(read_only=True)
    hotel_name = serializers.CharField(read_only=True, allow_null=True)
    booking_count = serializers.IntegerField(read_only=True)


class DepartmentMonthlySpendingSerializer(serializers.Serializer):
    department_id = serializers.IntegerField(read_only=True)
    department_name = serializers.CharField(read_only=True)
    month_trips = serializers.IntegerField(read_only=True)
    total_employees = serializers.IntegerField(read_only=True)
    month_spend = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)


class DashboardSummarySerializer(serializers.Serializer):
    """Serializer for the 4-card dashboard summary endpoint."""
    monthly_limit = serializers.DecimalField(max_digits=14, decimal_places=2)
    spent_this_month = serializers.DecimalField(max_digits=14, decimal_places=2)
    active_employees = serializers.IntegerField()
    pending_limit_requests = serializers.IntegerField()


class TravelPolicyRuleSerializer(serializers.Serializer):
    """Serializer for the per-department / per-employee travel limit rule."""
    id = serializers.IntegerField(read_only=True)
    policy_id = serializers.IntegerField(read_only=True)
    applies_to = serializers.ChoiceField(choices=["all", "department", "employee"])
    target_id = serializers.IntegerField(required=False, allow_null=True)
    target_name = serializers.CharField(read_only=True, allow_null=True)
    budget_limit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class TravelPolicyRuleCreateSerializer(serializers.Serializer):
    applies_to = serializers.ChoiceField(choices=["all", "department", "employee"])
    target_id = serializers.IntegerField(required=False, allow_null=True)
    budget_limit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)


class TravelPolicyRuleUpdateSerializer(serializers.Serializer):
    budget_limit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
