"""Seed a tenant schema with demo hotels for B2B search testing.

The B2B hotel search reads straight from a tenant's ``pms_property`` /
``pms_room`` tables, so exercising the filters (city, stars, price range,
guest capacity, dates, sorting) needs a data set with enough spread. This
command generates that spread deterministically — reruns with the same
``--count`` produce the same hotels.

    python manage.py seed_demo_hotels --count 60
    python manage.py seed_demo_hotels --count 100 --reset
"""

from __future__ import annotations

import random

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

# lat/lon of each city centre; hotels are jittered within a few km of it so
# the map pins spread out instead of stacking on one point.
CITIES: list[tuple[str, float, float]] = [
    ("Tashkent", 41.2995, 69.2401),
    ("Samarkand", 39.6270, 66.9750),
    ("Bukhara", 39.7747, 64.4286),
    ("Khiva", 41.3783, 60.3639),
    ("Namangan", 40.9983, 71.6726),
    ("Fergana", 40.3864, 71.7864),
    ("Andijan", 40.7821, 72.3442),
    ("Nukus", 42.4531, 59.6103),
    ("Urgench", 41.5506, 60.6317),
    ("Termez", 37.2242, 67.2783),
    ("Navoiy", 40.0844, 65.3792),
    ("Jizzakh", 40.1158, 67.8422),
    ("Qarshi", 38.8606, 65.7891),
]

BRANDS = [
    "Grand", "Royal", "Silk Road", "Registan", "Oriental", "Palace", "Plaza",
    "Continental", "Amir", "Sogdiana", "Zarafshan", "Chorsu", "Minor", "Lyabi",
    "Nurafshon", "Bek", "Safar", "Karvon", "Shodlik", "Zilol",
]
KINDS = ["Hotel", "Inn", "Resort", "Boutique Hotel", "Suites", "Guest House"]

STREETS = [
    "Amir Temur ko'chasi", "Navoiy ko'chasi", "Mustaqillik ko'chasi",
    "Islam Karimov ko'chasi", "Bobur ko'chasi", "Registon ko'chasi",
    "Shota Rustaveli ko'chasi", "Chilonzor ko'chasi", "Buyuk Ipak Yo'li",
    "Sharaf Rashidov shoh ko'chasi", "Furqat ko'chasi", "Beruniy ko'chasi",
]

CLASSES = ["standard", "essential", "comfort", "comfort_plus", "business", "premium", "signature"]

AMENITY_POOL = [
    "wifi", "parking", "breakfast", "pool", "spa", "gym", "restaurant",
    "bar", "conference", "laundry", "airport_shuttle", "air_conditioning",
]
THEME_POOL = ["business", "family", "romantic", "budget", "luxury", "historic"]

PHOTO_POOL = [
    "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1445019980597-93fa8acb246c?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1571003123894-1f0594d2b5d9?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1618773928121-c32242e63f39?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?auto=format&fit=crop&w=900&q=70",
    "https://images.unsplash.com/photo-1611892440504-42a792e24d32?auto=format&fit=crop&w=900&q=70",
]

# (label, preset, capacity, price multiplier) — capacity spread is what makes
# the "Кол-во человек" filter meaningful.
ROOM_TEMPLATES: list[tuple[str, str, int, float]] = [
    ("Standard Single", "single", 1, 0.75),
    ("Standard Double", "double", 2, 1.00),
    ("Twin", "twin", 2, 1.05),
    ("Deluxe", "deluxe", 3, 1.45),
    ("Family", "family", 4, 1.80),
    ("Suite", "suite", 5, 2.40),
    ("Presidential Suite", "suite", 6, 3.20),
]

GUEST_NAMES = [
    "Alisher T.", "Dilnoza K.", "Sardor M.", "Nigora A.", "Jasur R.",
    "Kamola S.", "Bekzod U.", "Malika Y.", "Otabek N.", "Zarina H.",
]

REVIEW_TEXTS = [
    "Отличный сервис и чистые номера.",
    "Хорошее расположение, удобно добираться до центра.",
    "Персонал очень вежливый, завтрак вкусный.",
    "Номер соответствует описанию, всё понравилось.",
    "Тихо и комфортно, вернусь снова.",
    "Неплохо за свои деньги.",
]

SEED_MARKER = "seeded_demo"


class Command(BaseCommand):
    help = "Seed a tenant schema with demo hotels (properties + rooms + reviews)."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=60, help="How many hotels to create (default 60).")
        parser.add_argument("--schema", type=str, default="hotel_demo", help="Tenant schema name (default hotel_demo).")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete hotels created by a previous run of this command first.",
        )

    def handle(self, *args, **options):
        count: int = options["count"]
        schema: str = options["schema"]
        reset: bool = options["reset"]

        if count < 1:
            raise CommandError("--count must be at least 1.")
        if not schema.replace("_", "").isalnum():
            raise CommandError(f"Refusing to use unsafe schema name: {schema!r}")

        rng = random.Random(20260724)

        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", [schema])
            if cur.fetchone() is None:
                raise CommandError(f"Schema {schema!r} does not exist.")

            cur.execute(f'SET search_path TO "{schema}", public')

            cur.execute("SELECT id FROM pms_property ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if row is None:
                raise CommandError(
                    f"Schema {schema!r} has no existing property to copy organization_id from."
                )
            cur.execute("SELECT organization_id FROM pms_property ORDER BY id LIMIT 1")
            organization_id = cur.fetchone()[0]

            if reset:
                # Seeded hotels are tagged in legal_info so a reset can never
                # touch real data that happens to share a name.
                cur.execute(
                    "SELECT id FROM pms_property WHERE legal_info->>'source' = %s",
                    [SEED_MARKER],
                )
                doomed = [r[0] for r in cur.fetchall()]
                if doomed:
                    cur.execute("DELETE FROM pms_review WHERE property_id = ANY(%s)", [doomed])
                    cur.execute("DELETE FROM pms_room WHERE property_id = ANY(%s)", [doomed])
                    cur.execute("DELETE FROM pms_property WHERE id = ANY(%s)", [doomed])
                self.stdout.write(f"Removed {len(doomed)} previously seeded hotel(s).")

            created = 0
            for index in range(count):
                city, base_lat, base_lon = CITIES[index % len(CITIES)]
                name = self._hotel_name(rng, index, city)

                star_rating = rng.choice([2, 3, 3, 4, 4, 4, 5, 5])
                # Nightly base price scales with stars, with noise, so the
                # price-range filter has hotels on both sides of any cutoff.
                base_price = {
                    2: rng.randrange(180_000, 320_000, 10_000),
                    3: rng.randrange(280_000, 520_000, 10_000),
                    4: rng.randrange(450_000, 900_000, 10_000),
                    5: rng.randrange(850_000, 2_200_000, 50_000),
                }[star_rating]

                lat = round(base_lat + rng.uniform(-0.045, 0.045), 6)
                lon = round(base_lon + rng.uniform(-0.055, 0.055), 6)
                address = f"{rng.choice(STREETS)} {rng.randint(1, 180)}"

                amenities = rng.sample(AMENITY_POOL, rng.randint(3, 7))
                themes = rng.sample(THEME_POOL, rng.randint(1, 3))
                photos = rng.sample(PHOTO_POOL, 3)

                cur.execute(
                    """
                    INSERT INTO pms_property (
                        organization_id, name,
                        description_uz, description_ru, description_en,
                        address, full_address, city, country,
                        latitude, longitude, star_rating, weel_classification,
                        themes, amenities, legal_info,
                        check_in_time, check_out_time, cancellation_policy,
                        photos, is_active, is_verified, is_recommended, verification_status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, 'UZ',
                        %s, %s, %s, %s, %s::text[], %s::text[], %s::jsonb,
                        '14:00', '12:00', 'free_until_24h',
                        %s::text[], TRUE, TRUE, %s, 'verified'
                    )
                    RETURNING id
                    """,
                    [
                        organization_id,
                        name,
                        f"{name} — qulay joylashuv, {city} markazida.",
                        f"{name} — удобное расположение в центре города {city}.",
                        f"{name} — comfortable stay in the heart of {city}.",
                        address,
                        f"{address}, {city}",
                        city,
                        lat,
                        lon,
                        star_rating,
                        rng.choice(CLASSES),
                        "{" + ",".join(themes) + "}",
                        "{" + ",".join(amenities) + "}",
                        f'{{"source": "{SEED_MARKER}"}}',
                        "{" + ",".join(f'"{p}"' for p in photos) + "}",
                        index % 5 == 0,
                    ],
                )
                property_id = cur.fetchone()[0]

                for room_index, (label, preset, capacity, multiplier) in enumerate(
                    self._rooms_for(rng, star_rating)
                ):
                    cur.execute(
                        """
                        INSERT INTO pms_room (
                            property_id, room_type_name, room_type_preset, room_number,
                            display_name, floor, capacity, meal_plan,
                            base_price, currency, photos, amenities,
                            availability, condition, is_active
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, 'UZS', %s::text[], %s::text[],
                            'available', 'clean', TRUE
                        )
                        """,
                        [
                            property_id,
                            label,
                            preset,
                            f"{preset[:3]}-{101 + room_index}",
                            label,
                            1 + room_index % 4,
                            capacity,
                            rng.choice(["BB", "HB", "RO"]),
                            int(base_price * multiplier),
                            "{" + ",".join(f'"{p}"' for p in rng.sample(PHOTO_POOL, 2)) + "}",
                            "{" + ",".join(rng.sample(AMENITY_POOL, 3)) + "}",
                        ],
                    )

                for _ in range(rng.randint(0, 14)):
                    cur.execute(
                        """
                        INSERT INTO pms_review (property_id, guest_name, rating, text, categories)
                        VALUES (%s, %s, %s, %s, '{}'::jsonb)
                        """,
                        [
                            property_id,
                            rng.choice(GUEST_NAMES),
                            rng.choice([3, 4, 4, 5, 5, 5]),
                            rng.choice(REVIEW_TEXTS),
                        ],
                    )

                created += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {created} hotel(s) into schema {schema!r}."))

    def _hotel_name(self, rng: random.Random, index: int, city: str) -> str:
        brand = BRANDS[index % len(BRANDS)]
        kind = KINDS[(index // len(BRANDS)) % len(KINDS)]
        return f"{brand} {city} {kind}"

    def _rooms_for(self, rng: random.Random, star_rating: int) -> list[tuple[str, str, int, float]]:
        """Higher-star hotels get the larger room types too, so filtering by
        guest count narrows the result set rather than emptying it."""
        pool_size = 3 if star_rating <= 2 else (5 if star_rating <= 4 else 7)
        return ROOM_TEMPLATES[:pool_size]
