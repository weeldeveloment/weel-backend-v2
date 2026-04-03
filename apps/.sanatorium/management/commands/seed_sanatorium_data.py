import logging

from django.core.management.base import BaseCommand

from sanatorium.models import (
    MedicalSpecialization,
    Treatment,
    RoomType,
    PackageType,
    RoomAmenity,
)

logger = logging.getLogger("..sanatorium")


SPECIALIZATIONS = [
    {"en": "Neurology", "ru": "Неврология", "uz": "Nevrologiya"},
    {"en": "Cardiology", "ru": "Кардиология", "uz": "Kardiologiya"},
    {"en": "Orthopedics", "ru": "Ортопедия", "uz": "Ortopediya"},
]

TREATMENTS = [
    {"en": "Massage", "ru": "Массаж", "uz": "Massaj"},
    {"en": "Physiotherapy", "ru": "Физиотерапия", "uz": "Fizioterapiya"},
    {"en": "Electrotherapy", "ru": "Электротерапия", "uz": "Elektroterapiya"},
    {"en": "Balneotherapy", "ru": "Бальнеотерапия", "uz": "Balneoterapiya"},
    {"en": "Peloidotherapy", "ru": "Пелоидотерапия", "uz": "Peloidoterapiya"},
    {"en": "Hot bath", "ru": "Горячая ванна", "uz": "Issig' vanna"},
    {"en": "Exercise therapy", "ru": "ЛФК", "uz": "LFK"},
    {"en": "Hydrotherapy", "ru": "Гидротерапия", "uz": "Gidroterapiya"},
    {"en": "Therapeutic shower", "ru": "Лечебный душ", "uz": "Shifobaxsh dush"},
    {"en": "Mineral baths", "ru": "Минеральные ванны", "uz": "Mineral vannalar"},
    {"en": "Mud therapy", "ru": "Грязелечение", "uz": "Loy bilan davolash"},
    {"en": "Therapeutic exercises", "ru": "Лечебные упражнения", "uz": "Davolovchi mashqlar"},
]

ROOM_TYPES = [
    {"en": "Lux", "ru": "Люкс", "uz": "Lux"},
    {"en": "Standard", "ru": "Стандарт", "uz": "Standard"},
    {"en": "Economy", "ru": "Эконом", "uz": "Ekonom"},
]

PACKAGE_TYPES = [
    {"en": "3 days", "ru": "3 дня", "uz": "3 kun", "days": 3},
    {"en": "7 days", "ru": "7 дней", "uz": "7 kun", "days": 7},
    {"en": "14 days", "ru": "14 дней", "uz": "14 kun", "days": 14},
]

AMENITIES = [
    "Parkovka", "Dush", "Vanna", "Konditsioner", "Wi-Fi", "CCTV kameralari",
    "Issiq suv", "Mangal", "Basseyn", "Sauna/hammom", "Jakuzi", "Garaj",
    "Futbol zonasi", "Elektr transportlar zaryadlash", "Alkov",
    "Isitish tizimi", "Televizor", "Kir yuvish mashinasi", "Kompyuter",
    "PlayStation", "Karaoke", "Fan", "Blender", "Tosh",
    "Tog'lar manzarasi", "Dush gol",
]


class Command(BaseCommand):
    help = "Seed ..sanatorium reference data: specializations, treatments, room types, packages, amenities"

    def handle(self, *args, **options):
        created_count = 0

        for spec in SPECIALIZATIONS:
            _, created = MedicalSpecialization.objects.get_or_create(
                title_en=spec["en"],
                defaults={"title_ru": spec["ru"], "title_uz": spec["uz"]},
            )
            if created:
                created_count += 1

        for treat in TREATMENTS:
            _, created = Treatment.objects.get_or_create(
                title_en=treat["en"],
                defaults={"title_ru": treat["ru"], "title_uz": treat["uz"]},
            )
            if created:
                created_count += 1

        for rt in ROOM_TYPES:
            _, created = RoomType.objects.get_or_create(
                title_en=rt["en"],
                defaults={"title_ru": rt["ru"], "title_uz": rt["uz"]},
            )
            if created:
                created_count += 1

        for pt in PACKAGE_TYPES:
            _, created = PackageType.objects.get_or_create(
                duration_days=pt["days"],
                defaults={
                    "title_en": pt["en"],
                    "title_ru": pt["ru"],
                    "title_uz": pt["uz"],
                },
            )
            if created:
                created_count += 1

        for amenity_name in AMENITIES:
            _, created = RoomAmenity.objects.get_or_create(
                title_uz=amenity_name,
                defaults={
                    "title_en": amenity_name,
                    "title_ru": amenity_name,
                },
            )
            if created:
                created_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Sanatorium seed data: {created_count} new records created")
        )
