# Weel API Documentation

## Umumiy ma'lumot

**Base URL:** `http://localhost:8000/api/`

**Authentication:** JWT token orqali amalga oshiriladi. Har bir so'rovda `Authorization: Bearer <token>` header bo'lishi kerak.

---

## 1. AUTHENTICATION API (Foydalanuvchilar)

Base: `/api/user/`

### 1.1 Client (Mijoz) Autentifikatsiya

#### OTP kod yuborish (Ro'yxatdan o'tish)
```http
POST /api/user/client/register/
```
**Request:**
```json
{
  "phone_number": "+998901234567",
  "first_name": "John",
  "last_name": "Doe"
}
```
**Response:**
```json
{
  "detail": "OTP sent successfully for registration",
  "phone_number": "+998901234567",
  "expires_in": "300 seconds"
}
```

#### OTP kodni qayta yuborish
```http
POST /api/user/client/register/resend/
```
**Request:**
```json
{
  "phone_number": "+998901234567"
}
```

#### Ro'yxatdan o'tishni tasdiqlash
```http
POST /api/user/client/register/verify/
```
**Request:**
```json
{
  "phone_number": "+998901234567",
  "otp": "123456",
  "fcm_token": "optional_device_token",
  "device_type": "android"
}
```
**Response:**
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "client": {
    "guid": "550e8400-e29b-41d4-a716-446655440000",
    "phone_number": "+998901234567",
    "first_name": "John",
    "last_name": "Doe"
  },
  "detail": "Registration completed successfully"
}
```

#### OTP kod yuborish (Login)
```http
POST /api/user/client/login/
```
**Request:**
```json
{
  "phone_number": "+998901234567"
}
```

#### Login ni tasdiqlash
```http
POST /api/user/client/login/verify/
```
**Request:**
```json
{
  "phone_number": "+998901234567",
  "otp": "123456",
  "fcm_token": "optional_device_token",
  "device_type": "android"
}
```

#### OTP qayta yuborish (Login)
```http
POST /api/user/client/login/resend/
```
**Request:**
```json
{
  "phone_number": "+998901234567"
}
```

#### Logout
```http
POST /api/user/client/logout/
Authorization: Bearer <token>
```
**Request:**
```json
{
  "refresh": "refresh_token_here"
}
```

#### Profil ma'lumotlari
```http
GET /api/user/client/profile/
Authorization: Bearer <token>
```

#### Profilni yangilash
```http
PUT /api/user/client/profile/update/
Authorization: Bearer <token>
```
**Request:**
```json
{
  "first_name": "John",
  "last_name": "Smith",
  "avatar": "file"
}
```

---

### 1.2 Partner (Hamkor) Autentifikatsiya

#### OTP kod yuborish (Ro'yxatdan o'tish)
```http
POST /api/user/partner/register/
```
**Request:**
```json
{
  "phone_number": "+998901234568",
  "username": "partner123",
  "first_name": "Jane",
  "last_name": "Doe",
  "email": "partner@example.com"
}
```

#### OTP qayta yuborish (Register)
```http
POST /api/user/partner/register/resend/
```

#### Ro'yxatdan o'tishni tasdiqlash
```http
POST /api/user/partner/register/verify/
```
**Request:**
```json
{
  "phone_number": "+998901234568",
  "otp": "123456",
  "fcm_token": "optional_device_token",
  "device_type": "ios"
}
```

#### OTP kod yuborish (Login)
```http
POST /api/user/partner/login/
```

#### OTP qayta yuborish (Login)
```http
POST /api/user/partner/login/resend/
```

#### Login ni tasdiqlash
```http
POST /api/user/partner/login/verify/
```

#### Partner profili
```http
GET /api/user/partner/profile/
Authorization: Bearer <token>
```

#### Profilni yangilash
```http
PUT /api/user/partner/profile/update/
Authorization: Bearer <token>
```

#### Passport yuklash
```http
POST /api/user/partner/documents/passport/
Authorization: Bearer <token>
Content-Type: multipart/form-data
```
**Form data:** `document` - fayl

#### Logout
```http
POST /api/user/partner/logout/
Authorization: Bearer <token>
```

---

### 1.3 Umumiy Autentifikatsiya

#### Token yangilash
```http
POST /api/user/refresh/
```
**Request:**
```json
{
  "refresh": "refresh_token_here"
}
```
**Response:**
```json
{
  "access": "new_access_token",
  "refresh": "new_refresh_token"
}
```

#### Akkauntni o'chirish (o'zingiznikini)
```http
DELETE /api/user/account/
Authorization: Bearer <token>
```
**Request:**
```json
{
  "refresh": "refresh_token_here"
}
```

---

## 2. PROPERTY API (Mulklar)

Base: `/api/property/`

### 2.1 Umumiy Endpointlar

#### Mulk turlari ro'yxati
```http
GET /api/property/types/
```
**Response:**
```json
[
  {
    "guid": "11111111-1111-1111-1111-111111111111",
    "title": "Apartment",
    "icon_url": null
  },
  {
    "guid": "22222222-2222-2222-2222-222222222222",
    "title": "Cottage",
    "icon_url": null
  }
]
```

#### Xizmatlar ro'yxati
```http
GET /api/property/services/
```

#### Mintaqa (Region) ro'yxati
```http
GET /api/property/regions/
```

#### Tuman (District) ro'yxati
```http
GET /api/property/districts/?region_id=1
```

#### Mahalla (Prefecture) ro'yxati
```http
GET /api/property/prefectures/?district_id=1
```

#### Lokatsiya daraxti (barcha region+district+prefecture)
```http
GET /api/property/location/
```

#### Tavsiyalar
```http
GET /api/property/recommendations/?kind=apartment&type=featured
```
**Query params:**
- `kind`: `property`, `apartment`, `cottage`
- `type`: `featured`, `best-by-reviews`, `most-booked`

---

### 2.2 Apartment API

#### Apartmentlar ro'yxati
```http
GET /api/property/apartments/
```

**Query parametrlar:**

| Parametr | Tavsif | Misol |
|----------|--------|-------|
| `search` | Qidiruv | `search=villa` |
| `region_id` | Mintaqa bo'yicha | `region_id=1` |
| `district_id` | Tuman bo'yicha | `district_id=5` |
| `corporate` | Korporativ uchun | `corporate=true` |
| `min_price` | Minimal narx | `min_price=100000` |
| `max_price` | Maksimal narx | `max_price=500000` |
| `currency` | Valyuta | `currency=UZS` |
| `sort` | Tartiblash | `sort=price_high` |
| `from_date` | Sana | `from_date=2024-01-15` |
| `limit` | Limit | `limit=20` |

**Sort qiymatlari:**
- `price_high` - Narx (yuqoridan pastga)
- `price_low` - Narx (pastdan yuqoriga)
- `rating_high` - Reyting (yuqori)
- `rating_low` - Reyting (past)
- `reviews_high` - Sharhlar soni (ko'p)
- `reviews_low` - Sharhlar soni (kam)
- `title_asc` - Sarlavha (A-Z)
- `title_desc` - Sarlavha (Z-A)
- `corporate_yes` - Faqat korporativ qabul qiladiganlar
- `corporate_no` - Faqat korporativ qabul qilmaydiganlar

**Response:**
```json
[
  {
    "guid": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Luxury Apartment",
    "img": ["http://localhost/media/property/img1.jpg"],
    "price": "250000.00",
    "currency": "UZS",
    "latitude": "41.2995",
    "longitude": "69.2401",
    "country": "UZ",
    "city": "Tashkent",
    "services": [],
    "region_id": 1,
    "district_id": 5,
    "prefecture_id": null,
    "guests": null,
    "rooms": null,
    "average_rating": 4.5,
    "is_favorite": false,
    "is_allowed_corporate": true,
    "created_at": "2024-01-15T10:30:00Z",
    "property_type_id": "11111111-1111-1111-1111-111111111111",
    "property_type": {
      "guid": "11111111-1111-1111-1111-111111111111",
      "title": "Apartment"
    }
  }
]
```

#### Yangi apartment yaratish
```http
POST /api/property/apartments/
Authorization: Bearer <partner_token>
```
**Request:**
```json
{
  "title": "New Apartment",
  "price": 250000,
  "currency": "UZS",
  "property_location": {
    "latitude": "41.2995",
    "longitude": "69.2401",
    "country": "Uzbekistan",
    "city": "Tashkent",
    "region_id": 1,
    "district_id": 5
  },
  "apartment_number": "12",
  "home_number": "5",
  "entrance_number": "2",
  "floor_number": "3",
  "pass_code": "1234",
  "property_detail": {
    "description_uz": "O'zbekcha tavsif",
    "description_ru": "Русское описание",
    "description_en": "English description",
    "check_in": "14:00:00",
    "check_out": "12:00:00",
    "is_allowed_alcohol": true,
    "is_allowed_corporate": true,
    "is_allowed_pets": false,
    "is_quiet_hours": true
  }
}
```

#### Apartment detali
```http
GET /api/property/apartments/{property_id}/
```
**Response:**
```json
{
  "guid": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Luxury Apartment",
  "img": ["http://localhost/media/property/img1.jpg"],
  "created_at": "2024-01-15T10:30:00Z",
  "currency": "UZS",
  "price": "250000.00",
  "minimum_weekend_day_stay": false,
  "weekend_only_sunday_inclusive": false,
  "description_en": "English text",
  "description_ru": "Русский текст",
  "description_uz": "O'zbekcha matn",
  "comment_count": 5,
  "average_rating": 4.5,
  "is_favorite": false,
  "services": [],
  "region_id": 1,
  "district_id": 5,
  "prefecture_id": null,
  "latitude": "41.2995",
  "longitude": "69.2401",
  "country": "UZ",
  "city": "Tashkent",
  "apartment_number": "12",
  "home_number": "5",
  "entrance_number": "2",
  "floor_number": "3",
  "pass_code": "1234",
  "check_in": "14:00:00",
  "check_out": "12:00:00",
  "is_allowed_alcohol": true,
  "is_allowed_corporate": true,
  "is_allowed_pets": false,
  "is_quiet_hours": true
}
```

#### Apartment yangilash
```http
PUT /api/property/apartments/{property_id}/
Authorization: Bearer <partner_token>
```

#### Apartment o'chirish
```http
DELETE /api/property/apartments/{property_id}/
Authorization: Bearer <partner_token>
```

#### Rasm qo'shish
```http
POST /api/property/apartments/{property_id}/images/
Authorization: Bearer <partner_token>
Content-Type: multipart/form-data
```
**Form data:** `images` - fayl(lar)

#### Rasm yangilash/o'chirish
```http
PATCH /api/property/apartments/{property_id}/images/{image_id}/
DELETE /api/property/apartments/{property_id}/images/{image_id}/
Authorization: Bearer <partner_token>
```

#### Sharhlar ro'yxati
```http
GET /api/property/apartments/{property_id}/reviews/
```

#### Sharh qo'shish
```http
POST /api/property/apartments/{property_id}/reviews/
Authorization: Bearer <client_token>
```
**Request:**
```json
{
  "rating": 4.5,
  "comment": "Ajoyib joy!"
}
```

#### Sevimlilarga qo'shish/o'chirish
```http
POST /api/property/apartments/{property_id}/favorite/
DELETE /api/property/apartments/{property_id}/favorite/
Authorization: Bearer <client_token>
```

---

### 2.3 Cottage API

#### Cottages ro'yxati
```http
GET /api/property/cottages/
```
**Query parametrlari:** apartment bilan bir xil

**Response:**
```json
[
  {
    "guid": "550e8400-e29b-41d4-a716-446655440001",
    "title": "Mountain Cottage",
    "img": ["http://localhost/media/property/cottage1.jpg"],
    "price_per_person": "50000.00",
    "price_on_working_days": "300000.00",
    "price_on_weekends": "400000.00",
    "currency": "UZS",
    "latitude": "41.5000",
    "longitude": "69.8000",
    "country": "UZ",
    "city": "Chimgan",
    "services": [],
    "region": {
      "id": 1,
      "guid": null,
      "title": "1",
      "img": null
    },
    "district": {
      "id": 5,
      "region_id": 1,
      "guid": null,
      "title": "5",
      "region": null
    },
    "prefecture_id": null,
    "guests": null,
    "rooms": null,
    "average_rating": 5.0,
    "is_favorite": false,
    "is_allowed_corporate": true,
    "created_at": "2024-01-15T10:30:00Z",
    "property_type_id": "22222222-2222-2222-2222-222222222222",
    "property_type": {
      "guid": "22222222-2222-2222-2222-222222222222",
      "title": "Cottage"
    }
  }
]
```

#### Yangi cottage yaratish
```http
POST /api/property/cottages/
Authorization: Bearer <partner_token>
```
**Request:**
```json
{
  "title": "New Cottage",
  "price_per_person": 50000,
  "price_on_working_days": 300000,
  "price_on_weekends": 400000,
  "currency": "UZS",
  "property_location": {
    "latitude": "41.5000",
    "longitude": "69.8000",
    "country": "Uzbekistan",
    "city": "Chimgan",
    "region_id": 1,
    "district_id": 5
  },
  "price": [
    {
      "month_from": "2024-01-01",
      "month_to": "2024-01-31",
      "price_per_person": 50000,
      "price_on_working_days": 300000,
      "price_on_weekends": 400000
    },
    {
      "month_from": "2024-02-01",
      "month_to": "2024-02-29",
      "price_per_person": 60000,
      "price_on_working_days": 350000,
      "price_on_weekends": 450000
    }
  ],
  "property_detail": {
    "description_uz": "O'zbekcha tavsif",
    "is_allowed_corporate": true
  }
}
```

#### Cottage detali
```http
GET /api/property/cottages/{property_id}/
```

#### Cottage yangilash
```http
PUT /api/property/cottages/{property_id}/
Authorization: Bearer <partner_token>
```

#### Cottage o'chirish
```http
DELETE /api/property/cottages/{property_id}/
Authorization: Bearer <partner_token>
```

#### Rasm qo'shish/yangilash/o'chirish
```http
POST /api/property/cottages/{property_id}/images/
PATCH /api/property/cottages/{property_id}/images/{image_id}/
DELETE /api/property/cottages/{property_id}/images/{image_id}/
Authorization: Bearer <partner_token>
```

#### Sharhlar
```http
GET /api/property/cottages/{property_id}/reviews/
POST /api/property/cottages/{property_id}/reviews/
Authorization: Bearer <client_token>
```

#### Sevimlilarga qo'shish
```http
POST /api/property/cottages/{property_id}/favorite/
DELETE /api/property/cottages/{property_id}/favorite/
Authorization: Bearer <client_token>
```

---

### 2.4 Partner API

#### Partner mulklari
```http
GET /api/property/partner/properties/?property_type=apartment
Authorization: Bearer <partner_token>
```
**Query params:** `property_type` - `apartment`, `cottage`, (bo'sh = hammasi)

#### Partner apartments
```http
GET /api/property/partner/apartments/
Authorization: Bearer <partner_token>
```

#### Partner cottages
```http
GET /api/property/partner/cottages/
Authorization: Bearer <partner_token>
```

#### Mulk statistikasi
```http
GET /api/property/partner/properties/{property_id}/analytics/?range=month
Authorization: Bearer <partner_token>
```
**Query params:** `range` - `week`, `month`, `quarter`, `year`

#### Partner sharhlari
```http
GET /api/property/apartments/{property_id}/partner/reviews/
GET /api/property/cottages/{property_id}/partner/reviews/
Authorization: Bearer <partner_token>
```

---

### 2.5 Favorites (Sevimli mulklar)

#### Sevimli mulklar ro'yxati
```http
GET /api/property/properties/favorites/
Authorization: Bearer <client_token>
```

#### Link bo'yicha filter
```http
POST /api/property/properties/filter-by-link/
```
**Request:**
```json
{
  "url": "https://example.com/property?region_id=1&min_price=100000"
}
```

---

## 3. BOOKING API (Bron qilish)

Base: `/api/booking/`

### 3.1 Client Booking

#### Mulk xonadoshligini ko'rish
```http
GET /api/booking/properties/{property_id}/calendar/?start_date=2024-01-01&end_date=2024-01-31
```

#### Bron qilish
```http
POST /api/booking/client/
Authorization: Bearer <client_token>
```
**Request:**
```json
{
  "property_id": "550e8400-e29b-41d4-a716-446655440000",
  "check_in": "2024-02-01",
  "check_out": "2024-02-03",
  "guests": 4,
  "note": "Additional request"
}
```

#### Mening bronlarim
```http
GET /api/booking/client/
Authorization: Bearer <client_token>
```

#### Bron detali
```http
GET /api/booking/client/{booking_id}/
Authorization: Bearer <client_token>
```

#### Bronni bekor qilish
```http
POST /api/booking/client/{booking_id}/cancel/
Authorization: Bearer <client_token>
```

#### Bron tarixi
```http
GET /api/booking/client/history/
Authorization: Bearer <client_token>
```

#### Tarix detali
```http
GET /api/booking/client/history/{booking_id}/
Authorization: Bearer <client_token>
```

---

### 3.2 Partner Booking

#### Mening bronlarim
```http
GET /api/booking/partner/
Authorization: Bearer <partner_token>
```

#### Bronni qabul qilish
```http
POST /api/booking/partner/{booking_id}/accept/
Authorization: Bearer <partner_token>
```

#### Bronni bekor qilish
```http
POST /api/booking/partner/{booking_id}/cancel/
Authorization: Bearer <partner_token>
```

#### Bronni yakunlash
```http
POST /api/booking/partner/{booking_id}/complete/
Authorization: Bearer <partner_token>
```

#### Mijoz kelmadini belgilash
```http
POST /api/booking/partner/{booking_id}/no_show/
Authorization: Bearer <partner_token>
```

---

### 3.3 Calendar Management (Partner)

#### Kunni band qilish
```http
POST /api/booking/properties/{property_id}/calendar/block/
Authorization: Bearer <partner_token>
```
**Request:**
```json
{
  "dates": ["2024-02-01", "2024-02-02"],
  "reason": "Renovation"
}
```

#### Bandligini olish
```http
POST /api/booking/properties/{property_id}/calendar/unblock/
Authorization: Bearer <partner_token>
```
**Request:**
```json
{
  "dates": ["2024-02-01"]
}
```

#### Kunni holdga olish
```http
POST /api/booking/properties/{property_id}/calendar/hold/
Authorization: Bearer <partner_token>
```

#### Holddan chiqarish
```http
POST /api/booking/properties/{property_id}/calendar/unhold/
Authorization: Bearer <partner_token>
```

---

## 4. STORIES API (Hikoyalar)

Base: `/api/story/`

### 4.1 Umumiy

#### Hikoyalar ro'yxati
```http
GET /api/story/public/stories/
```

#### Partner hikoyalari
```http
GET /api/story/partner/stories/
Authorization: Bearer <partner_token>
```

---

### 4.2 CRUD (Partner)

#### Hikoya yaratish
```http
POST /api/story/stories/
Authorization: Bearer <partner_token>
Content-Type: multipart/form-data
```
**Form data:**
- `media` - fayl (rasm/video)
- `type` - `image` yoki `video`

#### Hikoyalar ro'yxati
```http
GET /api/story/stories/
Authorization: Bearer <partner_token>
```

#### Hikoya detali
```http
GET /api/story/stories/{story_id}/
Authorization: Bearer <partner_token>
```

#### Hikoya yangilash
```http
PUT /api/story/stories/{story_id}/
Authorization: Bearer <partner_token>
```

#### Hikoya o'chirish
```http
DELETE /api/story/stories/{story_id}/
Authorization: Bearer <partner_token>
```

#### Media fayl olish
```http
GET /api/story/stories/{story_id}/{media_id}/
```

#### Media fayl o'chirish
```http
DELETE /api/story/stories/{story_id}/{media_id}/
Authorization: Bearer <partner_token>
```

---

## 5. NOTIFICATION API (Bildirishnomalar)

Base: `/api/notification/`

### 5.1 Client

#### FCM token yangilash
```http
POST /api/notification/device/
Authorization: Bearer <client_token>
```
**Request:**
```json
{
  "fcm_token": "device_token_here",
  "device_type": "android"
}
```

---

### 5.2 Partner

#### FCM token yangilash
```http
POST /api/notification/partner/device/
Authorization: Bearer <partner_token>
```

#### Bildirishnomalar ro'yxati
```http
GET /api/notification/partner/
Authorization: Bearer <partner_token>
```

#### O'qilgan deb belgilash
```http
POST /api/notification/partner/read/
Authorization: Bearer <partner_token>
```
**Request:**
```json
{
  "notification_ids": [1, 2, 3]
}
```

#### Hammasini o'qilgan deb belgilash
```http
POST /api/notification/partner/read-all/
Authorization: Bearer <partner_token>
```

---

## 6. CHAT API (Suhbatlar)

Base: `/api/chat/`

### 6.1 CRUD

#### Suhbatlar ro'yxati
```http
GET /api/chat/
Authorization: Bearer <token>
```

#### Yangi suhbat
```http
POST /api/chat/
Authorization: Bearer <token>
```

#### Suhbat detali
```http
GET /api/chat/{chat_id}/
Authorization: Bearer <token>
```

#### Suhbat yangilash
```http
PUT /api/chat/{chat_id}/
Authorization: Bearer <token>
```

#### Suhbat o'chirish
```http
DELETE /api/chat/{chat_id}/
Authorization: Bearer <token>
```

---

## 7. ADMIN AUTH API

Base: `/api/admin-auth/`

### 7.1 Autentifikatsiya

#### Admin login
```http
POST /api/admin-auth/login/
```
**Request:**
```json
{
  "username": "admin",
  "password": "password"
}
```

#### Admin ma'lumotlari
```http
GET /api/admin-auth/me/
Authorization: Bearer <admin_token>
```

#### Token yangilash
```http
POST /api/admin-auth/token/refresh/
```

#### Admin yaratish
```http
POST /api/admin-auth/register/
```

---

### 7.2 Foydalanuvchilar boshqaruvi

#### Clientlar ro'yxati
```http
GET /api/admin-auth/users/clients/
Authorization: Bearer <admin_token>
```

#### Partnerlar ro'yxati
```http
GET /api/admin-auth/users/partners/
Authorization: Bearer <admin_token>
```

---

## 8. BOT API

Base: `/api/bot/`

### 8.1 Webhook

#### Telegram webhook
```http
POST /api/bot/webhook/{secret_token}/
```

---

## ERROR RESPONSES (Xatolik javoblari)

### 400 Bad Request
```json
{
  "detail": "Invalid request data",
  "errors": {
    "field_name": ["Error message"]
  }
}
```

### 401 Unauthorized
```json
{
  "detail": "Authentication credentials were not provided."
}
```

### 403 Forbidden
```json
{
  "detail": "You do not have permission to perform this action."
}
```

### 404 Not Found
```json
{
  "detail": "Not found."
}
```

### 429 Too Many Requests
```json
{
  "detail": "Request was throttled."
}
```

### 500 Internal Server Error
```json
{
  "detail": "Internal server error."
}
```

---

## RATE LIMITING (So'rov cheklash)

| Endpoint | Limit |
|----------|-------|
| Login OTP send | 30/minute |
| Login OTP verify | 120/minute |
| Login OTP resend | 30/minute |
| Register OTP verify | 60/minute |
| Token refresh | 5000/second |
| Umumiy user | 120/minute |
| Anonim | 100/hour |

---

## QO'SHIMCHA MA'LUMOTLAR

### JWT Token
- **Access token** - 15 daqiqa amal qiladi
- **Refresh token** - 7 kun amal qiladi

### Valyutalar
- `UZS` - O'zbek so'mi
- `USD` - AQSh dollari

### Telefon raqami formati
- `+998901234567` - xalqaro formatda

### Sana formati
- `YYYY-MM-DD` - Masalan: `2024-01-15`

### Vaqt formati
- `HH:MM:SS` - Masalan: `14:30:00`
