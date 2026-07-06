# Hotel API — Mobile ilova uchun qo'llanma

Bu API'lar **public** — ya'ni chaqirish uchun token/login talab qilinmaydi
(`AllowAny`). Barcha endpoint'lar `GET` so'rovlari.

**Base URL:** `https://api.weel.uz/api/hotels/`

To'liq interaktiv hujjat (Swagger): `https://api.weel.uz/swagger/`

---

## 1. Mehmonxonalarni qidirish

```
GET /api/hotels/search/
```

### Query parametrlari (barchasi ixtiyoriy)

| Parametr | Turi | Izoh |
|---|---|---|
| `city` | string | Shahar nomi |
| `check_in` | date (`YYYY-MM-DD`) | Kirish sanasi |
| `check_out` | date (`YYYY-MM-DD`) | Chiqish sanasi (check_in dan keyin bo'lishi shart) |
| `guests` | int | Mehmonlar soni, default `1` |
| `star_rating` | int (1-5) | Yulduzlar soni |
| `weel_classification` | string | `standard`, `essential`, `comfort`, `comfort_plus`, `business`, `premium`, `signature` |
| `themes` | array[string] | Mavzular bo'yicha filter |
| `price_min` / `price_max` | decimal | Narx oralig'i |
| `sort_by` | string | `popular`, `rating`, `reviews`, `cheap`, `expensive` (default `popular`) |
| `page` | int | Sahifa raqami, default `1` |
| `page_size` | int | Sahifadagi elementlar soni, default `20`, max `100` |

### Javob (200)

```json
{
  "count": 42,
  "page": 1,
  "page_size": 20,
  "results": [
    {
      "id": 1,
      "name": "Hotel Uzbekistan",
      "city": "Tashkent",
      "full_address": "...",
      "star_rating": 4,
      "weel_classification": "comfort",
      "themes": ["business"],
      "description": "...",
      "rating": 4.5,
      "review_count": 120,
      "check_in_time": "14:00:00",
      "check_out_time": "12:00:00",
      "latitude": 41.31,
      "longitude": 69.28,
      "min_price": 500000.00
    }
  ]
}
```

---

## 2. Mehmonxona tafsilotlari

```
GET /api/hotels/{hotel_id}/
```

Yuqoridagi hotel card maydonlariga qo'shimcha:

- `amenities` — qulayliklar ro'yxati
- `wifi`, `parking`, `pool`, `restaurant`, `gym`, `pets_allowed`, `alcohol_allowed`, `quiet_hours` — boolean
- `images` — rasmlar ro'yxati
- `reviews` — so'nggi 5 ta sharh

Agar topilmasa: `404 {"detail": "Hotel not found."}`

---

## 3. Bo'sh xonalarni ko'rish

```
GET /api/hotels/{hotel_id}/rooms/
```

### Query parametrlari (majburiy)

| Parametr | Turi |
|---|---|
| `check_in` | date, majburiy |
| `check_out` | date, majburiy (check_in dan keyin) |
| `guests` | int, default `1` |

### Javob (200) — massiv

```json
[
  {
    "id": 10,
    "room_number": "101",
    "floor": 1,
    "display_name": "Deluxe Room",
    "bedroom_count": 1,
    "price_per_night": 500000.00,
    "beds": [{"type": "double", "count": 1}],
    "amenities": ["ac", "tv"],
    "capacity_adults": 2,
    "capacity_children": 1,
    "room_type_name": "Deluxe",
    "preset": "standard",
    "area_sqm": 25.5,
    "meal_plan": "breakfast",
    "images": ["https://..."]
  }
]
```

---

## 4. Xona narxini hisoblash

```
GET /api/hotels/{hotel_id}/rooms/{room_id}/price/
```

### Query parametrlari (majburiy)

| Parametr | Turi |
|---|---|
| `check_in` | date, majburiy |
| `check_out` | date, majburiy |

### Javob (200)

```json
{
  "nights": 3,
  "price_per_night": 500000.00,
  "total_price": 1500000.00,
  "hold_amount": 300000.00,
  "remaining_on_arrival": 1200000.00
}
```

Xona topilmasa yoki sanalar noto'g'ri bo'lsa: `400 {"detail": "Room not found or invalid dates."}`

---

## 5. Mehmonxona sharhlari

```
GET /api/hotels/{hotel_id}/reviews/
```

### Query parametrlari (ixtiyoriy)

| Parametr | Turi | Default |
|---|---|---|
| `limit` | int | `10` |
| `offset` | int | `0` |

### Javob (200) — massiv

```json
[
  {
    "id": 1,
    "guest_name": "Aziz",
    "rating": 4.5,
    "text": "Zo'r joy edi",
    "response": null,
    "created_at": "2026-06-01T10:00:00Z"
  }
]
```

---

## Eslatma

- Bu endpoint'lar public bo'lgani uchun API key kerak emas — to'g'ridan-to'g'ri chaqirish mumkin.
- Booking (bron qilish) va to'lov jarayonlari alohida, autentifikatsiya talab qiluvchi endpoint'lar orqali amalga oshiriladi (`/api/booking/`, `/api/payment/`) — bu hujjat faqat public hotel-browsing API'larini qamrab oladi.
