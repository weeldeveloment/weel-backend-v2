# Apartment yaratish -- API hujjatlari

**Endpoint:** `POST /api/properties/apartments/`

**Auth:** Partner JWT token (`Bearer <token>`)

---

## 1. Minimal request body (faqat majburiy maydonlar)

```json
{
  "title": "Tashkent City Apartment",
  "apartment_number": 12,
  "home_number": 45,
  "entrance_number": 3,
  "floor_number": 5,
  "pass_code": 1234,
  "description_ru": "Уютная квартира в центре города",
  "description_uz": "Shahar markazida qulay kvartira",
  "check_in": "14:00:00",
  "check_out": "12:00:00",
  "is_allowed_alcohol": false,
  "is_allowed_corporate": true,
  "is_allowed_pets": false,
  "is_quiet_hours": true,
  "guests": 4,
  "rooms": 2,
  "beds": 2,
  "bathrooms": 1
}
```

Agar faqat shuni yuborsangiz, server avtomatik quyidagilarni qo'yadi:
- `price`: `0`
- `currency`: `UZS`
- `img`: `[]`
- `services`: `[]`

---

## 2. To'liq request body (barcha maydonlar bilan)

```json
{
  "title": "Tashkent City Apartment",
  "price": 150000,
  "currency": "UZS",

  "latitude": "41.2995",
  "longitude": "69.2401",
  "country": "Uzbekistan",
  "city": "Tashkent",
  "region_id": 1,
  "district_id": 75,
  "prefecture_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",

  "apartment_number": 12,
  "home_number": 45,
  "entrance_number": 3,
  "floor_number": 5,
  "pass_code": 1234,

  "description_en": "Cozy apartment in the city center",
  "description_ru": "Уютная квартира в центре города",
  "description_uz": "Shahar markazida qulay kvartira",

  "check_in": "14:00:00",
  "check_out": "12:00:00",

  "is_allowed_alcohol": false,
  "is_allowed_corporate": true,
  "is_allowed_pets": false,
  "is_quiet_hours": true,

  "services": [
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222"
  ],

  "guests": 4,
  "rooms": 2,
  "beds": 2,
  "bathrooms": 1,

  "img": [
    "https://example.com/apartment1.jpg",
    "https://example.com/apartment2.jpg"
  ]
}
```

---

## 3. Muhim qoidalar

| Maydon | Tavsif |
|--------|--------|
| `title` | **Majburiy** |
| `price` | Ixtiyoriy. Berilmasa `0` bo'ladi |
| `currency` | `USD` yoki `UZS`. Default: `UZS` |
| `latitude`, `longitude` | Ixtiyoriy. Decimal (masalan `41.2995`) |
| `apartment_number` | **Majburiy** — kvartira raqami |
| `home_number` | **Majburiy** -- uy raqami |
| `entrance_number` | **Majburiy** -- kirish raqami |
| `floor_number` | **Majburiy** -- qavat raqami |
| `pass_code` | **Majburiy** -- eshik kodi |
| `description_ru` | **Majburiy** -- rus tilida tavsif |
| `description_uz` | **Majburiy** -- o'zbek tilida tavsif |
| `description_en` | Ixtiyoriy, lekin berilsa bo'sh bo'lmasligi kerak |
| `check_in`, `check_out` | **Majburiy** -- format `HH:MM:SS` |
| `is_allowed_alcohol` | **Majburiy** -- boolean |
| `is_allowed_corporate` | **Majburiy** -- boolean |
| `is_allowed_pets` | **Majburiy** -- boolean |
| `is_quiet_hours` | **Majburiy** -- boolean |
| `guests` | **Majburiy** -- mehmonlar soni |
| `rooms` | **Majburiy** -- xonalar soni |
| `beds` | **Majburiy** -- to'shaklar soni |
| `bathrooms` | **Majburiy** -- hammomlar soni |
| `district_id` | Agar `75` (Toshkent shahri) yoki `82` (Samarqand) bo'lsa, `prefecture_id` ham majburiy |
| `services` | Xizmat UUID larining ro'yxati |
| `img` | Rasm URL/patlarining ro'yxati |

---

## 4. Xato xabarlari (rus tilida)

| Maydon | Xato xabari |
|--------|-------------|
| `title` | `Укажите название.` / `Название не может быть пустым.` |
| `price` | `Введите корректную цену.` / `Цена не может быть меньше 0.` |
| `currency` | `Выберите корректную валюту.` |
| `latitude` | `Некорректная широта.` |
| `longitude` | `Некорректная долгота.` |
| `region_id` | `Некорректный регион.` |
| `district_id` | `Некорректный район.` |
| `prefecture_id` | `Некорректная префектура.` |
| `apartment_number` | `Укажите номер квартиры.` |
| `home_number` | `Укажите номер дома.` |
| `entrance_number` | `Укажите подъезд.` |
| `floor_number` | `Укажите этаж.` |
| `pass_code` | `Укажите код доступа.` |
| `description_ru` | `Добавьте описание на русском языке.` / `Описание на русском языке не может быть пустым.` |
| `description_uz` | `Добавьте описание на узбекском языке.` / `Описание на узбекском языке не может быть пустым.` |
| `description_en` | `Описание на английском языке не может быть пустым.` |
| `check_in` | `Укажите время заезда.` / `Неверный формат времени (чч:мм:сс).` |
| `check_out` | `Укажите время выезда.` / `Неверный формат времени (чч:мм:сс).` |
| `guests` | `Укажите количество гостей.` |
| `rooms` | `Укажите количество комнат.` |
| `beds` | `Укажите количество кроватей.` |
| `bathrooms` | `Укажите количество ванных комнат.` |
| `prefecture_id` (district 75/82) | `Укажите префектуру для выбранного района.` |
| `prefecture_id` (not linked) | `Выбранная префектура не соответствует району.` |
| `prefecture_id` (wrong district) | `Префектура недоступна для выбранного района.` |
