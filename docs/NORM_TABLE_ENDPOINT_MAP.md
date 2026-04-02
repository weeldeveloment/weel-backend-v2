# API endpointlar ↔ `norm_*` jadvallar (mantiqiy moslik)

Barcha yo‘llar prefix: **`/api/`**  
14 ta asosiy jadval: `norm_customers`, `norm_partners`, `norm_client_sessions`, `norm_client_devices`, `norm_partner_sessions`, `norm_partner_devices`, `norm_properties`, `norm_property_prices`, `norm_bookings`, `norm_booking_status_history`, `norm_booking_payment_links`, `norm_exchange_rates`, `norm_notifications`, `norm_payment_transactions`.

Quyidagi jadvalda **qaysi endpoint qaysi norm jadvalga yoziladi / o‘qiladi** (yoki bevosita bog‘lanmaydi) ko‘rsatilgan.

---

## 1. `norm_customers` — mijoz (client)

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| POST | `/api/user/client/register/` | Yangi mijoz (OTP yuborish) |
| POST | `/api/user/client/register/resend/` | OTP qayta |
| POST | `/api/user/client/register/verify/` | Ro‘yxatdan o‘tish, **customer yaratish** |
| POST | `/api/user/client/login/` | Login OTP |
| POST | `/api/user/client/login/resend/` | OTP qayta |
| POST | `/api/user/client/login/verify/` | **Sessiya** (asosan `norm_client_sessions`) |
| POST | `/api/user/client/logout/` | Sessiyani tugatish |
| GET | `/api/user/client/profile/` | **O‘qish** |
| PATCH/PUT | `/api/user/client/profile/update/` | **Yangilash** |
| * | `/api/user/client/cards/` (CRUD) | Kartalar — agar normda alohida bo‘lmasa, `norm_customers` yoki alohida dizayn |
| POST | `/api/user/refresh/` | Token yangilash → `norm_client_sessions` bilan bog‘liq |

---

## 2. `norm_client_sessions` — client JWT / sessiya

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| POST | `/api/user/client/login/verify/` | Access/refresh token berish |
| POST | `/api/user/refresh/` | Yangi access |
| POST | `/api/user/client/logout/` | Sessiyani invalidatsiya (blacklist bo‘lsa token_blacklist) |

---

## 3. `norm_client_devices` — client FCM / qurilma

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| POST | `/api/notification/device/` | FCM token, **device qatori** |

---

## 4. `norm_partners` — hamkor (partner)

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| POST | `/api/user/partner/register/` | Partner OTP |
| POST | `/api/user/partner/register/resend/` | |
| POST | `/api/user/partner/register/verify/` | **Partner yaratish** |
| POST | `/api/user/partner/login/` | |
| POST | `/api/user/partner/login/resend/` | |
| POST | `/api/user/partner/login/verify/` | **Partner sessiya** |
| POST | `/api/user/partner/logout/` | |
| GET | `/api/user/partner/profile/` | **O‘qish** |
| PATCH | `/api/user/partner/profile/update/` | **Yangilash** |
| POST | `/api/user/partner/documents/passport/` | Hujjat — passport maydonlari `norm_partners` yoki alohida |
| GET | `/api/admin-auth/users/partners/` | Admin ro‘yxat — **o‘qish** |

---

## 5. `norm_partner_sessions` — partner sessiya

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| POST | `/api/user/partner/login/verify/` | Partner token |
| POST | `/api/user/refresh/` | Agar partner refresh ishlatilsa |

---

## 6. `norm_partner_devices` — partner FCM

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| POST | `/api/notification/partner/device/` | Partner qurilma tokeni |

---

## 7. `norm_exchange_rates` — valyuta kursi (USD/UZS)

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| * | **Har qayerda narx USD→UZS aylantiriladigan** | Masalan: |
| GET | `/api/property/properties/` | `exchange_rate()` — **o‘qish** |
| GET | `/api/property/recommendations/` | xuddi shu |
| GET | `/api/property/categories/.../properties/` | xuddi shu |
| * | Boshqa property list endpointlari | kurs **o‘qiladi** |

*(Kursni yozish odatda Celery: `payment.tasks.update_exchange_rate` — API orqali emas.)*

---

## 8. `norm_properties` — obyektlar (listing)

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| GET | `/api/property/properties/` | Ro‘yxat |
| POST | `/api/property/properties/` | **Yaratish** (partner) |
| GET/PATCH/DELETE | `/api/property/properties/<uuid>/` | Batafsil / tahrirlash / o‘chirish |
| GET | `/api/property/properties/apartments/` | Apartamentlar |
| GET | `/api/property/properties/cottages/` | Kottejlar |
| GET | `/api/property/partner/properties/` | Partnerning obyektlari |
| GET | `/api/property/regions/<id>/properties/` | Hudud bo‘yicha |
| GET | `/api/property/properties/filter-by-link/` | Filter |
| GET | `/api/property/categories/<id>/properties/` | Kategoriya |
| GET | `/api/property/categories/<id>/properties/latest/` | So‘nggi |
| GET | `/api/admin-auth/users/clients/` | bevosita emas; client ro‘yxati |

Rasm, joylashuv, xizmatlar, kategoriya — agar normda denormalizatsiya bo‘lsa hammasi `norm_properties` ichida; aks holda `property_*` jadvallar bilan sinxron saqlanadi.

---

## 9. `norm_property_prices` — narxlar (kunlik / turi)

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| * | `/api/property/properties/` POST/PATCH | Narx qatorlari bilan birga |
| * | Booking flow | Band qilingan sanalar → `booking_*` + mantiqan **narx** `norm_property_prices` yoki booking jadvalida |

Aniq REST yo‘li ko‘p hollarda property ichida — **narx o‘zgarishlari** `norm_property_prices` ga map qilinadi.

---

## 10. `norm_bookings` — bronlar (umumiy)

**Turar-joy (property):**

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| GET/POST | `/api/booking/client/` | Ro‘yxat / **yangi bron** |
| GET | `/api/booking/client/<uuid>/` | Batafsil |
| GET | `/api/booking/client/history/` | Tarix |
| GET | `/api/booking/client/history/<uuid>/` | Tarix batafsil |
| POST | `/api/booking/client/<uuid>/cancel/` | Bekor |
| GET | `/api/booking/partner/` | Partner bronlari |
| POST | `/api/booking/partner/<uuid>/accept/` | Qabul |
| POST | `/api/booking/partner/<uuid>/cancel/` | Bekor |
| POST | `/api/booking/partner/<uuid>/complete/` | Yakun |
| POST | `/api/booking/partner/<uuid>/no_show/` | Kelmaslik |
| GET | `/api/booking/admin/bookings/` | Admin ro‘yxat |

**Sanatorium:**

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| GET/POST | `/api/sanatorium/booking/client/` | **Bron yaratish / ro‘yxat** |
| GET | `/api/sanatorium/booking/client/history/` | Tarix |
| GET | `/api/sanatorium/booking/client/<uuid>/` | Batafsil |
| POST | `/api/sanatorium/booking/client/<uuid>/cancel/` | Bekor |
| GET | `/api/sanatorium/booking/partner/` | Partner |
| POST | `.../accept/`, `.../cancel/`, `.../complete/`, `.../no_show/` | Statuslar |

*Agar `norm_bookings` faqat property uchun bo‘lsa, sanatorium alohida jadvalda qoladi; agar bitta umumiy bron jadval bo‘lsa — `booking_type` maydoni bilan ajratiladi.*

---

## 11. `norm_booking_status_history` — status o‘zgarishlari

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| POST | `/api/booking/partner/<uuid>/accept/` | **History qator** |
| POST | `/api/booking/partner/<uuid>/cancel/` | |
| POST | `/api/booking/partner/<uuid>/complete/` | |
| POST | `/api/booking/partner/<uuid>/no_show/` | |
| POST | `/api/booking/client/<uuid>/cancel/` | |
| POST | `/api/sanatorium/booking/partner/.../accept|cancel|complete|no_show/` | |
| POST | `/api/sanatorium/booking/client/.../cancel/` | |

Kalendar hold/block/unhold — asosan `booking_calendardate` / sanatorium calendar; status tarixiga ixtiyoriy yozuv.

---

## 12. `norm_booking_payment_links` — to‘lov havolalari / to‘lov holati

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| * | To‘lov yoki Plum integratsiyasi qayerda bo‘lsa | **Havola / external_id** |
| * | `/api/booking/...` ichida pending to‘lov | Mantiqan shu jadval |

*(Aniq URL kodda payment link endpoint bo‘lmasa, faqat task/service orqali to‘ldiriladi.)*

---

## 13. `norm_payment_transactions` — to‘lov tranzaksiyalari

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| * | Plum / hold / charge callback yoki ichki servis | **Tranzaksiya yozuvi** |
| * | Booking to‘lovi tasdiqlanganda | |

REST orqali to‘g‘ridan-to‘g‘ri endpoint ko‘p loyihalarda yo‘q — webhook / Celery.

---

## 14. `norm_notifications` — bildirishnomalar

| Metod | Endpoint | Vazifa |
|-------|----------|--------|
| GET | `/api/notification/partner/` | **O‘qish** (partner) |
| POST | `/api/notification/partner/read/` | O‘qilgan deb belgilash |
| POST | `/api/notification/partner/read-all/` | Hammasi |
| * | Push yuborish (server ichki) | **Yozish** — booking, sanatorium, bot va hokazo |

---

## `norm_*` bilan bevosita bog‘lanmaydigan endpointlar

| Modul | Endpointlar | Izoh |
|-------|-------------|------|
| **Story** | `/api/story/stories/`, `partner/stories/`, `public/stories/` | `stories_*` jadvallar |
| **Chat** | `/api/chat/conversations/`, `messages/`, `send/`, `read/` | `chat_*` |
| **Bot** | `/api/bot/webhook/.../` | Telegram |
| **Admin-auth** | `/api/admin-auth/login/`, `me/`, `token/refresh/`, `register/` | `auth_user` + JWT |
| **Logs** | `/api/logs/frontend/` | log |
| **Property spravochnik** | `types/`, `regions/`, `districts/`, `services/`, `categories/`, `location/` | spravochnik — normda alohida bo‘lmasligi mumkin |
| **Sanatorium spravochnik** | `specializations/`, `treatments/`, `room-types/`, va hokazo | `sanatorium_*` |
| **Booking kalendar** | `/api/booking/properties/<id>/calendar/...` | bandlik sanalari — `norm_bookings` bilan bog‘liq lekin jadval `booking_calendardate` |
| **Sanatorium kalendar** | `.../rooms/.../calendar/...` | `sanatorium_roomcalendardate` |

---

## Qisqa xulosa

| `norm_*` jadval | Asosiy API guruhlari |
|-----------------|----------------------|
| `norm_customers` | `/api/user/client/*` |
| `norm_client_sessions` | login verify, refresh, logout |
| `norm_client_devices` | `/api/notification/device/` |
| `norm_partners` | `/api/user/partner/*`, admin partners list |
| `norm_partner_sessions` | partner login verify, refresh |
| `norm_partner_devices` | `/api/notification/partner/device/` |
| `norm_exchange_rates` | property list/recommendations (o‘qish) + Celery yozish |
| `norm_properties` | `/api/property/properties/*`, partner list, categories |
| `norm_property_prices` | property CRUD + booking narx mantig‘i |
| `norm_bookings` | `/api/booking/*`, `/api/sanatorium/booking/*` |
| `norm_booking_status_history` | accept/cancel/complete/no_show/cancel |
| `norm_booking_payment_links` | to‘lov havolasi (service/webhook) |
| `norm_payment_transactions` | Plum / to‘lov servislari |
| `norm_notifications` | `/api/notification/partner/*` + ichki push |

Bu hujjat **mantiqiy moslik** uchun; aniq ustunlar va FK lar sizning `norm_*` sxemangizga qarab mapping qilinadi.
