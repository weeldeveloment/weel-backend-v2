# Norm datastore (`USE_NORM_DATASTORE`)

## Yoqish

```bash
# .env yoki Dokploy
USE_NORM_DATASTORE=1
```

`norm_*` jadvallar PostgreSQLda mavjud bo‘lishi kerak (sizning sxemangiz bilan mos).

## Nima qiladi

| Qism | Harakat |
|------|---------|
| **Valyuta** | `exchange_rate()` va Celery `update_exchange_rate` → `norm_exchange_rates` |
| **Client FCM** | `POST /api/notification/device/` → `norm_client_devices` (+ `norm_customers`) |
| **Partner FCM** | `POST /api/notification/partner/device/` → `norm_partner_devices` (+ `norm_partners`) |
| **Push yuborish** | Tokenlar `norm_*_devices` dan olinadi |
| **Property** | Har `save` da `norm_properties`, `norm_property_prices` |
| **Booking** | Har o‘zgarishda `norm_bookings`, status o‘zgarganda `norm_booking_status_history` |
| **Plum** | `norm_payment_transactions` |

## Bir martalik backfill

Mavjud Django ma’lumotlarini norm ga ko‘chirish:

```bash
USE_NORM_DATASTORE=1 python manage.py sync_norm_from_django
```

## O‘chirish

`USE_NORM_DATASTORE=0` yoki o‘zgaruvchini olib tashlang — avvalgi `users_client`, `client_devices`, `payment_exchangerate` mantig‘i ishlaydi.
