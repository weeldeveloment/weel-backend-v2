"""Xodim qo'shishda ma'lumotlar qo'lda kiritiladi (passport rasmisiz).

Ilgari ism/familiya, passport seriyasi va PINFL yuklangan SHAXS
GUVOHNOMASI rasmlaridan OCR orqali o'qilardi; endi ular so'rovda keladi va
serializer darajasida tekshiriladi."""
from apps.b2b.serializers import B2BEmployeeCreateSerializer


def _payload(**overrides):
    data = {
        "first_name": "Abbos",
        "last_name": "Setdarov",
        "passport_series": "AD4779438",
        "passport_pinfl": "51309076810024",
        "department_id": 4,
        "email": "abbos@example.com",
        "phone": "+998 90 111 11 11",
    }
    data.update(overrides)
    return data


def test_manual_fields_are_accepted_without_any_passport_upload():
    serializer = B2BEmployeeCreateSerializer(data=_payload())
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["first_name"] == "Abbos"
    assert serializer.validated_data["last_name"] == "Setdarov"
    assert serializer.validated_data["passport_pinfl"] == "51309076810024"
    # Telefon raqami bo'sh joysiz saqlanadi — performer login aynan shu
    # qiymat bo'yicha topiladi.
    assert serializer.validated_data["phone"] == "+998901111111"


def test_every_manual_field_is_required():
    serializer = B2BEmployeeCreateSerializer(data={"department_id": 4})
    assert not serializer.is_valid()
    for field in ("first_name", "last_name", "passport_series", "passport_pinfl", "email", "phone"):
        assert field in serializer.errors


def test_passport_series_is_normalised_to_upper_case():
    serializer = B2BEmployeeCreateSerializer(data=_payload(passport_series="ad 4779438"))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["passport_series"] == "AD4779438"


def test_passport_series_must_match_the_id_card_format():
    for value in ("A4779438", "AD477943", "AD47794388", "ADD779438"):
        serializer = B2BEmployeeCreateSerializer(data=_payload(passport_series=value))
        assert not serializer.is_valid(), value
        assert "passport_series" in serializer.errors


def test_pinfl_must_be_exactly_fourteen_digits():
    for value in ("5130907681002", "513090768100245", "5130907681002A"):
        serializer = B2BEmployeeCreateSerializer(data=_payload(passport_pinfl=value))
        assert not serializer.is_valid(), value
        assert "passport_pinfl" in serializer.errors


def test_spaces_around_a_name_do_not_become_part_of_it():
    serializer = B2BEmployeeCreateSerializer(data=_payload(first_name="  Abbos   Shuxrat  "))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["first_name"] == "Abbos Shuxrat"


def test_blank_name_is_rejected_rather_than_stored_empty():
    serializer = B2BEmployeeCreateSerializer(data=_payload(last_name="   "))
    assert not serializer.is_valid()
    assert "last_name" in serializer.errors
