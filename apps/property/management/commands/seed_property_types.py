"""
PropertyType va PropertyService larni default qiymatlar bilan yaratadi.
Bazada tiplar bo‘lmasa property yaratishda "Property type with this GUID doesn't exist" xatoligi chiqadi.
Ishlatish: python manage.py seed_property_types
"""
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from property.models import PropertyType, PropertyService


# Minimal SVG (icon uchun)
DEFAULT_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"></svg>'

# Default property types (title_en view da "Cottages" qidiriladi)
PROPERTY_TYPES = [
    {"title_en": "Apartment", "title_ru": "Квартира", "title_uz": "Kvartira"},
    {"title_en": "Cottages", "title_ru": "Коттеджи", "title_uz": "Kottejlar"},
    {"title_en": "House", "title_ru": "Дом", "title_uz": "Uy"},
]

# Har bir type uchun default servislar (key = title_en)
SERVICES_BY_TYPE = {
    "Apartment": [
        {"title_en": "Wi-Fi", "title_ru": "Wi-Fi", "title_uz": "Wi-Fi"},
        {"title_en": "Parking", "title_ru": "Парковка", "title_uz": "Parkovka"},
    ],
    "Cottages": [
        {"title_en": "Wi-Fi", "title_ru": "Wi-Fi", "title_uz": "Wi-Fi"},
        {"title_en": "Barbecue", "title_ru": "Мангал", "title_uz": "Barbekyu"},
    ],
    "House": [
        {"title_en": "Wi-Fi", "title_ru": "Wi-Fi", "title_uz": "Wi-Fi"},
        {"title_en": "Parking", "title_ru": "Парковка", "title_uz": "Parkovka"},
    ],
}


class Command(BaseCommand):
    help = "PropertyType va PropertyService larni default qiymatlar bilan yaratadi (boshida bir marta ishlatiladi)."

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        created_count = 0
        service_count = 0

        for data in PROPERTY_TYPES:
            title_en = data["title_en"]
            pt = PropertyType.objects.filter(title_en=title_en).first()
            if pt is None:
                pt = PropertyType(
                    title_en=data["title_en"],
                    title_ru=data["title_ru"],
                    title_uz=data["title_uz"],
                )
                pt.icon.save("default.svg", ContentFile(DEFAULT_SVG), save=True)
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"PropertyType yaratildi: {title_en} (guid={pt.guid})")
                )
            else:
                self.stdout.write(f"PropertyType mavjud: {title_en} (guid={pt.guid})")

            services_data = SERVICES_BY_TYPE.get(title_en, [])
            for sdata in services_data:
                ps = PropertyService.objects.filter(
                    property_type=pt,
                    title_en=sdata["title_en"],
                ).first()
                if ps is None:
                    ps = PropertyService(
                        property_type=pt,
                        title_en=sdata["title_en"],
                        title_ru=sdata["title_ru"],
                        title_uz=sdata["title_uz"],
                    )
                    ps.icon.save("default.svg", ContentFile(DEFAULT_SVG), save=True)
                    service_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Tugadi: {created_count} ta PropertyType, {service_count} ta PropertyService yangi yaratildi."
            )
        )
        self.stdout.write(
            "Property yaratishdan oldin: GET /api/property/types/ — property_type_id olish uchun, "
            "GET /api/property/services/ — barcha servicelar; ?property_id=<guid> — shu property ga tegishli servicelar."
        )
