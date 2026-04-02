"""
Oʻzbekiston viloyatlari va tumanlari roʻyxatini yuklaydi.
Property qoʻshishda region/district tanlash va filter uchun ishlatiladi.

Ishlatish:
  python manage.py load_uz_regions_districts
  python manage.py load_uz_regions_districts --file apps/property/data/uz_regions_districts.json

Birinchi marta migratsiyani ishlatib, keyin bu commandni ishlating.
"""
import json
import os

from django.core.management.base import BaseCommand

from property.models import Region, District


# Standart viloyat nomlari (Oʻzbekiston boʻylab)
DEFAULT_REGIONS = [
    {"title_uz": "Andijon", "title_ru": "Андижан", "title_en": "Andijan"},
    {"title_uz": "Buxoro", "title_ru": "Бухара", "title_en": "Bukhara"},
    {"title_uz": "Fargʻona", "title_ru": "Фергана", "title_en": "Fergana"},
    {"title_uz": "Jizzax", "title_ru": "Джизак", "title_en": "Jizzakh"},
    {"title_uz": "Namangan", "title_ru": "Наманган", "title_en": "Namangan"},
    {"title_uz": "Navoiy", "title_ru": "Навои", "title_en": "Navoiy"},
    {"title_uz": "Navoiy shahri", "title_ru": "Город Навои", "title_en": "Navoiy City"},
    {"title_uz": "Qashqadaryo", "title_ru": "Кашкадарья", "title_en": "Kashkadarya"},
    {"title_uz": "Qoraqalpogʻiston", "title_ru": "Каракалпакстан", "title_en": "Karakalpakstan"},
    {"title_uz": "Samarqand", "title_ru": "Самарканд", "title_en": "Samarkand"},
    {"title_uz": "Sirdaryo", "title_ru": "Сырдарья", "title_en": "Sirdaryo"},
    {"title_uz": "Surxondaryo", "title_ru": "Сурхандарья", "title_en": "Surkhandarya"},
    {"title_uz": "Toshkent", "title_ru": "Ташкентская область", "title_en": "Tashkent Region"},
    {"title_uz": "Toshkent shahri", "title_ru": "Город Ташкент", "title_en": "Tashkent City"},
    {"title_uz": "Xorazm", "title_ru": "Хорезм", "title_en": "Khorezm"},
]

# Har bir viloyat boʻyicha tumanlar (Wikipedia va rasmiy manbalar asosida)
DEFAULT_DISTRICTS_BY_REGION = {
    "Toshkent shahri": [
        "Akmal Ikromov", "Bektemir", "Chilonzor", "Hamza", "Mirobod", "Mirzo Ulugʻbek",
        "Olmazor", "Sergeli", "Shayxontohur", "Sobir Rahimov", "Uchtepa", "Yakkasaroy",
        "Yashnaobod", "Yunusobod",
    ],
    "Qoraqalpogʻiston": [
        "Amudaryo", "Beruniy", "Boʻzatov", "Chimboy", "Ellikqala", "Kegeyli", "Moʻynoq",
        "Nukus", "Qanlikoʻl", "Qoʻngʻirot", "Qoraoʻzak", "Shumanay", "Taxtakoʻpir",
        "Toʻrtkoʻl", "Xoʻjayli",
    ],
    "Andijon": [
        "Andijon", "Asaka", "Baliqchi", "Boʻzsuv", "Buloqboshi", "Izboskan", "Jalolquduq",
        "Marhamat", "Oltinkoʻl", "Paxtaobod", "Qoʻrgʻontepa", "Ulugʻnor", "Xoʻjaobod", "Xonobod",
    ],
    "Fargʻona": [
        "Beshariq", "Bogʻdod", "Buvayda", "Dangʻara", "Fargʻona", "Furqat", "Oltiariq",
        "Oxunboboev", "Oʻzbekiston", "Qoʻshtepa", "Quva", "Quvasoy", "Rishton", "Soʻx",
        "Toshloq", "Uchkoʻprik", "Yozyovon",
    ],
    "Namangan": [
        "Chortoq", "Chust", "Kosonsoy", "Mingbuloq", "Namangan", "Norin", "Pop",
        "Toʻraqoʻrgʻon", "Uchqoʻrgʻon", "Uychi", "Yangiqoʻrgʻon",
    ],
    "Toshkent": [
        "Angren", "Bekobod", "Boʻka", "Boʻstonliq", "Chinoz", "Chirchiq", "Ohangaron",
        "Olmaliq", "Oqqoʻrgʻon", "Oʻrtachirchiq", "Parkent", "Piskent", "Qibray",
        "Toshkent", "Yangiyoʻl", "Yuqorichirchiq", "Zangiota",
    ],
    "Samarqand": [
        "Bulungʻur", "Ishtixon", "Jomboy", "Kattaqoʻrgʻon", "Narpay", "Nurobod", "Oqdaryo",
        "Pastdargʻom", "Paxtachi", "Payariq", "Qoʻshrabot", "Samarqand", "Toyloq", "Urgut",
    ],
    "Buxoro": [
        "Buxoro", "Gʻijduvon", "Jondor", "Kogon", "Olot", "Peshku", "Qorakoʻl",
        "Qorovulbozor", "Romitan", "Shofirkon", "Vobkent",
    ],
    "Xorazm": [
        "Bogʻot", "Gurlan", "Hazorasp", "Pitnak", "Qoʻshkoʻpir", "Shovot", "Urganch",
        "Xiva", "Xonqa", "Yangiariq", "Yangibozor",
    ],
    "Surxondaryo": [
        "Angor", "Bandixon", "Boysun", "Denov", "Jarqoʻrgʻon", "Muzrabot", "Oltinsoy",
        "Qiziriq", "Qumqoʻrgʻon", "Sariosiyo", "Sherobod", "Shoʻrchi", "Termiz", "Uzun",
    ],
    "Qashqadaryo": [
        "Bahoriston", "Chiroqchi", "Dehqonobod", "Gʻuzor", "Kasbi", "Kitob", "Koson",
        "Muborak", "Nishon", "Qamashi", "Qarshi", "Shahrisabz", "Usmon Yusupov", "Yakkabogʻ",
    ],
    "Jizzax": [
        "Arnasoy", "Baxmal", "Doʻstlik", "Forish", "Gʻallaorol", "Jizzax", "Mirzachoʻl",
        "Paxtakor", "Yangiobod", "Zafarobod", "Zarbdor", "Zomin",
    ],
    "Sirdaryo": [
        "Baxt", "Boyovut", "Guliston", "Mehnatobod", "Mirzaobod", "Oqoltin", "Sayxunobod",
        "Sharof Rashidov", "Sirdaryo", "Xovos", "Yangiyer",
    ],
    "Navoiy": [
        "Karmana", "Konimex", "Navbahor", "Navoiy", "Nurota", "Qiziltepa", "Tomdi",
        "Uchquduq", "Xatirchi", "Zarafshon",
    ],
    "Navoiy shahri": [
        "Karmana",
    ],
}

def load_from_dict(regions_data, districts_by_region, verbosity=1):
    """Regions va Districts ni yaratadi (yoki yangilaydi)."""
    region_by_title_uz = {}
    created_regions = 0
    for r in regions_data:
        obj, created = Region.objects.get_or_create(
            title_uz=r["title_uz"],
            defaults={"title_ru": r["title_ru"], "title_en": r["title_en"]},
        )
        region_by_title_uz[r["title_uz"]] = obj
        if created:
            created_regions += 1

    created_districts = 0
    for region_title_uz, district_names in districts_by_region.items():
        region = region_by_title_uz.get(region_title_uz)
        if not region:
            if verbosity > 0:
                print(f"Viloyat topilmadi (tumanlar uchun): {region_title_uz}")
            continue
        for name_uz in district_names:
            _, created = District.objects.get_or_create(
                region=region,
                title_uz=name_uz,
                defaults={"title_ru": name_uz, "title_en": name_uz},
            )
            if created:
                created_districts += 1

    return created_regions, created_districts

class Command(BaseCommand):
    help = "Oʻzbekiston viloyatlari va tumanlarini Region va District jadvallariga yuklaydi (filter va property uchun)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=None,
            help="JSON fayl yoʻli (boʻlmasa default roʻyxat ishlatiladi). Format: "
                 '{"regions": [{"title_uz","title_ru","title_en"}], "districts": [{"region_title_uz","title_uz",...}]}',
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Avval barcha Region va District ni oʻchirib, qayta yuklash (ehtiyotkorlik bilan).",
        )

    def handle(self, *args, **options):
        verbosity = options.get("verbosity", 1)
        file_path = options.get("file")
        clear = options.get("clear", False)

        if clear:
            District.objects.all().delete()
            Region.objects.all().delete()
            if verbosity > 0:
                self.stdout.write("Region va District tozalandi.")

        if file_path:
            if not os.path.isfile(file_path):
                self.stderr.write(self.style.ERROR(f"Fayl topilmadi: {file_path}"))
                return
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            regions_data = data.get("regions", [])
            districts_data = data.get("districts", [])
            # districts: [{"region_title_uz": "...", "title_uz": "...", "title_ru": "...", "title_en": "..."}]
            region_by_title_uz = {}
            for r in regions_data:
                obj, created = Region.objects.get_or_create(
                    title_uz=r["title_uz"],
                    defaults={"title_ru": r.get("title_ru", r["title_uz"]), "title_en": r.get("title_en", r["title_uz"])},
                )
                region_by_title_uz[r["title_uz"]] = obj
            created_regions = sum(
                1 for r in regions_data
                if Region.objects.filter(title_uz=r["title_uz"]).count() == 1
            )
            created_districts = 0
            for d in districts_data:
                region = region_by_title_uz.get(d["region_title_uz"])
                if not region:
                    continue
                _, created = District.objects.get_or_create(
                    region=region,
                    title_uz=d["title_uz"],
                    defaults={
                        "title_ru": d.get("title_ru", d["title_uz"]),
                        "title_en": d.get("title_en", d["title_uz"]),
                    },
                )
                if created:
                    created_districts += 1
            if verbosity > 0:
                self.stdout.write(self.style.SUCCESS(
                    f"JSON dan yuklandi: {len(regions_data)} viloyat, {len(districts_data)} tuman. "
                    f"Yangi: ~{created_districts} tuman."
                ))
            return

        created_regions, created_districts = load_from_dict(
            DEFAULT_REGIONS, DEFAULT_DISTRICTS_BY_REGION, verbosity=verbosity
        )
        if verbosity > 0:
            self.stdout.write(self.style.SUCCESS(
                f"Yuklandi: yangi {created_regions} viloyat, {created_districts} tuman. "
                "GET /api/property/regions/ va GET /api/property/districts/?region_id=<guid> orqali olish mumkin."
            ))
