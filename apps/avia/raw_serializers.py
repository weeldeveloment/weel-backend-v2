from __future__ import annotations

from rest_framework import serializers

from apps.avia.models import DocumentType, Gender, PassengerAge, ServiceClass


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class DirectionSerializer(serializers.Serializer):
    departure_airport = serializers.CharField(min_length=3, max_length=3)
    arrival_airport = serializers.CharField(min_length=3, max_length=3)
    date = serializers.DateField()

    def validate(self, attrs):
        if attrs["departure_airport"].upper() == attrs["arrival_airport"].upper():
            raise serializers.ValidationError(
                "Departure and arrival airports must differ."
            )
        return attrs


class OfferSearchSerializer(serializers.Serializer):
    """A route plus who is flying. One direction is one-way, two is a return."""

    directions = DirectionSerializer(many=True, min_length=1, max_length=6)
    service_class = serializers.ChoiceField(
        choices=sorted(ServiceClass.ALL), default=ServiceClass.ECONOMY
    )
    adults = serializers.IntegerField(min_value=1, max_value=9)
    children = serializers.IntegerField(min_value=0, max_value=9, default=0)
    infants = serializers.IntegerField(min_value=0, max_value=9, default=0)
    infants_with_seat = serializers.IntegerField(min_value=0, max_value=9, default=0)

    def validate(self, attrs):
        # Bookhara rejects these upstream too, but a 400 from us costs nothing
        # and names the actual problem instead of returning an error code.
        adults = attrs["adults"]
        if attrs["infants"] > adults:
            raise serializers.ValidationError(
                "There cannot be more lap infants than adults."
            )
        total_seats = adults + attrs["children"] + attrs["infants_with_seat"]
        if total_seats > 9:
            raise serializers.ValidationError(
                "A single booking can hold at most 9 seated passengers."
            )
        dates = [d["date"] for d in attrs["directions"]]
        if dates != sorted(dates):
            raise serializers.ValidationError(
                "Direction dates must be in chronological order."
            )
        return attrs

    def to_provider_params(self) -> dict:
        data = self.validated_data
        return {
            "directions": [
                {
                    "departure_airport": d["departure_airport"].upper(),
                    "arrival_airport": d["arrival_airport"].upper(),
                    "date": d["date"].isoformat(),
                }
                for d in data["directions"]
            ],
            "service_class": data["service_class"],
            "adults": data["adults"],
            "children": data["children"],
            "infants": data["infants"],
            "infants_with_seat": data["infants_with_seat"],
        }


class ScheduleQuerySerializer(serializers.Serializer):
    departure_from = serializers.DateField()
    departure_to = serializers.DateField()
    airport_from = serializers.CharField(min_length=3, max_length=3, required=False)
    airport_to = serializers.CharField(min_length=3, max_length=3, required=False)
    airlines = serializers.ListField(
        child=serializers.CharField(min_length=2, max_length=3),
        required=False,
        allow_empty=True,
    )

    def validate(self, attrs):
        if attrs["departure_to"] < attrs["departure_from"]:
            raise serializers.ValidationError(
                "departure_to cannot be earlier than departure_from."
            )
        return attrs


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

class PassengerSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=120)
    last_name = serializers.CharField(max_length=120)
    # Bookhara wants a single space when the document carries no patronymic —
    # omitting the field entirely is the other accepted form, and an empty
    # string is neither.
    middle_name = serializers.CharField(max_length=120, required=False, allow_null=True)
    age = serializers.ChoiceField(choices=sorted(PassengerAge.ALL))
    birthdate = serializers.DateField()
    gender = serializers.ChoiceField(choices=sorted(Gender.ALL))
    citizenship = serializers.CharField(min_length=2, max_length=2)
    tel = serializers.CharField(max_length=32)
    doc_type = serializers.CharField(max_length=8, default=DocumentType.UNIVERSAL)
    doc_number = serializers.CharField(max_length=64)
    doc_expire = serializers.DateField()


def passenger_to_provider(data: dict) -> dict:
    """Shape one validated passenger the way Bookhara's booking body wants it."""
    payload = {
        "first_name": data["first_name"],
        "last_name": data["last_name"],
        "age": data["age"],
        "birthdate": data["birthdate"].isoformat(),
        "gender": data["gender"],
        "citizenship": data["citizenship"].upper(),
        "tel": data["tel"],
        "doc_type": data["doc_type"],
        "doc_number": data["doc_number"],
        "doc_expire": data["doc_expire"].isoformat(),
    }
    if data.get("middle_name"):
        payload["middle_name"] = data["middle_name"]
    return payload


class CreateBookingSerializer(serializers.Serializer):
    payer_name = serializers.CharField(max_length=255)
    payer_email = serializers.EmailField()
    payer_tel = serializers.RegexField(
        r"^\+\d{9,15}$",
        error_messages={"invalid": "Phone must be in international format, e.g. +998901234567."},
    )
    order_note = serializers.CharField(max_length=64, required=False, allow_blank=True)
    passengers = PassengerSerializer(many=True, min_length=1, max_length=9)
    additional_services = serializers.ListField(
        child=serializers.CharField(max_length=64), required=False, allow_empty=True
    )

    def provider_passengers(self) -> list[dict]:
        return [passenger_to_provider(p) for p in self.validated_data["passengers"]]


class B2BCreateBookingSerializer(CreateBookingSerializer):
    """A corporate booking is attached to a trip, and often to an employee."""

    trip_id = serializers.IntegerField(required=False, allow_null=True)
    employee_id = serializers.IntegerField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

class AviaBookingPassengerSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    passenger_key = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    middle_name = serializers.CharField(read_only=True, allow_null=True)
    age_group = serializers.CharField(read_only=True)
    gender = serializers.CharField(read_only=True, allow_null=True)
    birthdate = serializers.DateField(read_only=True, allow_null=True)
    citizenship = serializers.CharField(read_only=True, allow_null=True)
    doc_type = serializers.CharField(read_only=True, allow_null=True)
    doc_number = serializers.CharField(read_only=True, allow_null=True)
    doc_expire = serializers.DateField(read_only=True, allow_null=True)
    price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True)
    tickets = serializers.JSONField(read_only=True)
    itinerary_receipt_url = serializers.CharField(read_only=True, allow_null=True)


class AviaBookingSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.UUIDField(read_only=True)
    provider_booking_id = serializers.CharField(read_only=True)
    booking_number = serializers.CharField(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True)
    offer_type = serializers.CharField(read_only=True, allow_null=True)
    flight_type = serializers.CharField(read_only=True, allow_null=True)
    fare_family_type = serializers.CharField(read_only=True, allow_null=True)
    is_charter = serializers.BooleanField(read_only=True)
    refund_availability = serializers.BooleanField(read_only=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True)
    prev_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True)
    currency = serializers.CharField(read_only=True, allow_null=True)
    payer_name = serializers.CharField(read_only=True, allow_null=True)
    payer_email = serializers.CharField(read_only=True, allow_null=True)
    payer_tel = serializers.CharField(read_only=True, allow_null=True)
    b2b_trip_id = serializers.IntegerField(read_only=True, allow_null=True)
    b2b_employee_id = serializers.IntegerField(read_only=True, allow_null=True)
    provider_created_at = serializers.DateTimeField(read_only=True, allow_null=True)
    expires_at = serializers.DateTimeField(read_only=True, allow_null=True)
    directions = serializers.JSONField(read_only=True)
    information_for_clients = serializers.JSONField(read_only=True)
    additional_services = serializers.JSONField(read_only=True, allow_null=True)
    fiscalization = serializers.JSONField(read_only=True, allow_null=True)
    passengers = AviaBookingPassengerSerializer(many=True, read_only=True, required=False)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
