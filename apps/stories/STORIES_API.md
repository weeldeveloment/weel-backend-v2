# Stories API — clientlarga chiqarish va foydalanish

Barcha URL’lar `/api/story/` prefiksi ostida (masalan: `http://127.0.0.1:8000/api/story/...`).

---

## Client uchun (ilova / brauzer)

### 1. Ro‘yxat — property type bo‘yicha (parametr majburiy)

| Method | URL | Auth | Qanday ishlatish |
|--------|-----|------|-------------------|
| GET | `/api/story/public/stories/?property_type=<uuid>` | Shart emas | **Parametrsiz so‘rov 404 Not Found.** Faqat `property_type` bilan. |
| GET | `/api/story/stories/?property_type=<uuid>` | Ixtiyoriy (client token) | Client uchun ham **property_type majburiy**; parametrsiz 404. |

**Query parametrlar:**

- `property_type` (majburiy) — Property type GUID. Parametr yuborilmasa 404. Faqat shu turdagi property’lar uchun story’lar qaytadi (boshqa turdagi story’lar chiqmaydi).

**Javob:** `200 OK`, body — story’lar ro‘yxati. Har bir story’da:

- `guid`, `property_id`, `property_title`, `property_type_guid`, `property_image_url`, `media`

**Client’ga chiqishi uchun story qoidalari:**

- `is_verified == true` (admin’da verify qilingan)
- `expires_at` hozirgi vaqtdan keyin (odatda 48 soat)
- Property arxivlanmagan (`is_archived == false`)

Agar `[]` qaytsa: story’lar admin’da verify qilinmagan, muddati o‘tgan yoki faqat arxivlangan property’lar uchun.

### 2. Bitta story media — ko‘rish / view hisoblash

| Method | URL | Auth |
|--------|-----|------|
| GET | `/api/story/stories/<story_id>/<media_id>/` | Ixtiyoriy (client token bo‘lsa view hisoblanadi) |

---

## Partner uchun

### 3. O‘z story’lari ro‘yxati

| Method | URL | Auth |
|--------|-----|------|
| GET | `/api/story/partner/stories/` | Partner JWT |

Verify qilinmaganlar ham ko‘rinadi (muddati o‘tmaganlar).

### 4. Story yaratish (media qo‘shish)

| Method | URL | Auth |
|--------|-----|------|
| POST | `/api/story/stories/` | Partner JWT |

Body (multipart): `property_id` (UUID), `media_type` (image/video), `media_file` (file).

Property type alohida saqlanmaydi: story `property_id` orqali Property’ga bog‘lanadi, Property o‘zida `property_type` ga ega. Shuning uchun `property_type_guid` javobda `property.property_type.guid` dan keladi.

### 5. Story / media o‘chirish

| Method | URL | Auth |
|--------|-----|------|
| DELETE | `/api/story/stories/<story_id>/` | Partner (egasi) |
| DELETE | `/api/story/stories/<story_id>/<media_id>/` | Partner (egasi) |

---

## Property type GUID olish

Client tur bo‘yicha filtrlash yoki ko‘rsatish uchun type ro‘yxatini oladi:

| Method | URL |
|--------|-----|
| GET | `/api/property/types/` |

Javobda har bir type uchun `guid` bor — shu `guid` ni `?property_type=<guid>` sifatida stories endpoint’iga yuboriladi.

---

## Qisqacha

- **Client list:** `GET /api/story/public/stories/` (parametrsiz yoki `?property_type=<guid>`).
- **Property type saqlash:** Story’da alohida saqlanmaydi; `property_id` orqali Property → `property_type`. Javobda `property_type_guid` endi chiqadi.
- **Bo‘sh `[]` sabablari:** Story verify qilinmagan, muddati o‘tgan yoki property arxivlangan. Admin’da Stories → tanlash → “Verify selected stories”.
