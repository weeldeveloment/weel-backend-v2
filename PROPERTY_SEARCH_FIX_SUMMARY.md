# Property Search Fix — Jamoa uchun qisqa xulosa

## 1. Muammo nima edi?

Front/Mobile da `location_id` UUID ko‘rinishida yuborilayotgan edi, lekin backend uni faqat **integer** sifatida qabul qilayotgan edi.

**Misol:**
```
GET /api/property/properties?location_id=7bba2a3d-f3be-4f31-9fdf-2e83bc220045
```

**Oldingi kod:**
```python
"region_id": _parse_int(
    _source_get(source, "region_id") or _source_get(source, "location_id")
),
```

`_parse_int("7bba2a3d...")` → `None` qaytaradi.

**Natija:** `location_id` UUID bo‘lsa, **location filtri umuman ishlamaydi** va barcha apartment/cottage lar qaytib keladi (noto‘g‘ri natija).

---

## 2. Nimani tuzatdik?

### A) `location_id` endi UUID ni ham to‘g‘ri resolve qiladi
Ketma-ketlik:
1. Integer bo‘lsa → `region_id`
2. Region GUID bo‘lsa → `region_id`
3. District GUID bo‘lsa → `district_id`
4. Prefecture GUID bo‘lsa → `prefecture_id`

### B) `prefecture_id` filtri qo‘shildi
Apartment va Cottage qidiruviga alohida `prefecture_id` bo‘yicha filter qo‘shildi.

### C) `kind` parametrini qo‘shildi
`/api/property/properties` endpointida endi `kind=cottage` ham ishlaydi (`property_type` ga alternative).

---

## 3. Front/Mobile ga tasir qiladimi?

**Yo‘q, backward compatible.**

| Eski xatti-harakat | Hali ham ishlaydimi? |
|---|---|
| `location_id=123` (int) | ✅ Ha, avvalgidek |
| `region_id=1` | ✅ Ha, birinchilikda ishlatiladi |
| `district_id=1` | ✅ Ha, birinchilikda ishlatiladi |
| `property_type=cottage` | ✅ Ha, avvalgidek |
| `location_id=UUID` | ✅ **Endi to‘g‘ri ishlaydi** (avval ishlamagan) |

**Yagona o‘zgarish:** UUID `location_id` yuborilganda endi to‘g‘ri filter qiladi. Eski integer-based kod o‘zgarmaydi.

---

## 4. Qaysi fayllar o‘zgardi?

- `apps/property/views.py`
- `apps/property/apartment_repository.py`
- `apps/property/cottage_repository.py`

---

## 5. Test qilish uchun

```bash
# Region UUID bilan
GET /api/property/properties?location_id=7bba2a3d-f3be-4f31-9fdf-2e83bc220045

# Prefecture UUID bilan
GET /api/property/apartments/?prefecture_id=prefecture-guid

# Cottage filteri bilan
GET /api/property/properties?property_type=cottage&location_id=xxx
```
