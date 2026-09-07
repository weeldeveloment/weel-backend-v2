# PgBouncer — o'rnatish

Nima uchun: backend har bir so'rovda PostgreSQL'ga **yangi ulanish** ochadi
(`CONN_MAX_AGE=0`). O'lchandi (2026-09-07):

| | vaqt |
|---|---|
| ulanish ochib `SELECT 1` (hozirgi holat) | **13,69 ms** |
| saqlangan ulanishda `SELECT 1` | **0,21 ms** |

Bu lokal bazaga. Bu narx **har bir endpointga** tushadi — `/me/` ning 197 ms
bo'lishining bir sababi ham shu.

`CONN_MAX_AGE` ni shundoq ko'tarib bo'lmaydi: ASGI/Daphne har bir ish oqimida
bittadan ulanish to'playdi va `max_connections` ni tugatadi. PgBouncer aynan
shuni yechadi.

---

## 0-qadam: sig'im hisobi — `max_connections = 100`

Standart qiymatlar aynan shu son uchun tanlangan, o'zgartirish shart emas.

| Kim | Ulanish |
|---|---|
| **PgBouncer** (`MAX_DB_CONNECTIONS`) | **30** |
| postgres-exporter (`AUTO_DISCOVER_DATABASES=true`, har bazaga bittadan) | ~10 |
| Grafana (hozir sqlite3, postgresga o'tsa) | ~5 |
| `psql`, zaxira nusxa, qo'lda ulanishlar | ~5 |
| Deploy paytida hali eski yo'l bilan ulangan konteynerlar | ~20 |
| superuser zaxirasi (`superuser_reserved_connections`) | 3 |
| **Jami eng yomon holat** | **~73 / 100** |

27 ta zaxira qoladi. PgBouncer'ning o'zi 30 dan oshib keta olmaydi — bu
qat'iy shift, shuning uchun bazani tugatib qo'yishi mumkin emas.

Nega 20 ta pool yetadi: transaction rejimida ulanish faqat tranzaksiya
davomida band bo'ladi. Chat so'rovi 7 ta so'rov yuboradi, har biri ~1 ms —
20 ta slot yuzlab bir vaqtdagi ilova ulanishiga xizmat qiladi.

Deploydan oldin ikkitasini tasdiqlang:

```sql
SHOW password_encryption;                    -- md5 chiqsa → PGB_AUTH_TYPE=md5
SELECT count(*) FROM pg_stat_activity;       -- hozirgi yuk, 73 ga joy bormi
```

## 1-qadam: PgBouncer'ni deploy qiling — backend'ga hali tegmasdan

Dokploy'da yangi **Compose** ilova (Type = "Docker Compose", Stack emas),
manba shu `pgbouncer/` papkasi. Environment:

```
DB_HOST=<hozirgi Postgres host — backend'dagi bilan bir xil>
DB_PORT=5432
DB_USER=<hozirgi>
DB_PASSWORD=<hozirgi>
DOKPLOY_NETWORK=dokploy-network
```

Boshqa hech narsa shart emas — standart qiymatlar `max_connections = 100`
uchun sozlangan: `PGB_DEFAULT_POOL_SIZE=20`, `PGB_MAX_DB_CONNECTIONS=30`,
`PGB_MAX_CLIENT_CONN=1000`, `PGB_AUTH_TYPE=scram-sha-256`.

> `AUTH_TYPE` bazangiz nimadan foydalanayotganiga mos bo'lishi kerak.
> Tekshirish: `SHOW password_encryption;` — `md5` chiqsa, `PGB_AUTH_TYPE=md5`.

> **Sinovdan o'tgan (2026-09-07).** Bu stack lokal Postgres'ga qarshi
> haqiqatan ishga tushirilib tekshirildi: config to'g'ri yaratiladi
> (`pool_mode = transaction`, `listen_port = 6432`), Django undan o'tib
> so'rov yuboradi, `transaction.atomic()` ishlaydi, `SHOW POOLS` javob
> beradi va exporter `pgbouncer_up 1` chiqaradi. O'lchandi: saqlangan
> ulanishda `SELECT 1` — 0,39 ms, har safar qayta ulanib — 10,3 ms.

Deploydan keyin **hali backend'ga tegmasdan** tekshiring:

```bash
docker exec -it <pgbouncer-konteyner> \
  psql "postgres://$DB_USER:$DB_PASSWORD@127.0.0.1:6432/$DB_NAME" -c "SELECT 1"
```

Javob kelmasa — pastdagi "Nima buzilishi mumkin" ga qarang. Backend hali
eski yo'l bilan ishlayapti, hech narsa yiqilmagan.

## 2-qadam: backend'ni PgBouncer'ga o'tkazing

Backend ilovasining environment'ida **faqat shu ikkitasini** o'zgartiring:

```
DB_HOST=pgbouncer
DB_PORT=6432
DB_POOLED=1        # ← YANGI: server-side kursorlarni o'chiradi va
                   #    CONN_MAX_AGE ni 600 ga ko'taradi
```

`DB_NAME`, `DB_USER`, `DB_PASSWORD` — o'zgarmaydi.

`DB_POOLED=1` **shart**. Usiz `CONN_MAX_AGE` 0 bo'lib qoladi va PgBouncer
hech qanday foyda bermaydi.

Redeploy qiling. Tekshiring: `/api/b2b/workspace/me/` javob berishi va
javob vaqti tushishi kerak.

## 3-qadam: kuzating

Grafana'da (monitoring stack'ga `pgbouncer-exporter` scrape target'i
qo'shildi):

- `pgbouncer_pools_client_waiting_connections` — **0 bo'lib turishi kerak.**
  Doim 0 dan yuqori bo'lsa, pool kichik: `PGB_DEFAULT_POOL_SIZE` ni 20 → 30
  va `PGB_MAX_DB_CONNECTIONS` ni 30 → 40 qiling (yuqoridagi jadvalda hali
  27 ta zaxira bor, ya'ni bu xavfsiz). Undan yuqorisiga chiqishdan oldin
  `max_connections` ni ko'tarish kerak.
- `pgbouncer_pools_server_active_connections` — bazaga haqiqiy ulanishlar.
- Backend'ning javob vaqti (`weel-backend` job) — tushishi kerak.

---

## Orqaga qaytarish

Backend environment'ida uchta qatorni eski holiga qaytaring:

```
DB_HOST=<eski Postgres host>
DB_PORT=5432
DB_POOLED=0
```

Redeploy. PgBouncer stack'i o'chirilmasa ham zarar qilmaydi — unga hech kim
ulanmaydi. Kod tomonida hech narsani qaytarish shart emas: `DB_POOLED`
yoqilmagan bo'lsa, sozlamalar bir belgigacha bugungidek qoladi
(tekshirilgan).

---

## Nima buzilishi mumkin

| Belgi | Sabab | Yechim |
|---|---|---|
| `unsupported startup parameter: ...` | psycopg2 PgBouncer tanimaydigan parametr yuboryapti | O'sha parametrni `IGNORE_STARTUP_PARAMETERS` ga qo'shing |
| `password authentication failed` | `AUTH_TYPE` bazanikiga mos emas | `SHOW password_encryption;` ga qarang, `PGB_AUTH_TYPE` ni moslang |
| So'rovlar sekinlashdi, `client_waiting` > 0 | pool kichik | `PGB_DEFAULT_POOL_SIZE` ni ko'taring |
| `query_wait_timeout` xatolari | pool to'lgan yoki baza sekin | yuqoridagidek; `pg_stat_activity` da uzoq so'rovlarni qidiring |
| `cursor "..." does not exist` | kimdir `.iterator()` ishlatibdi | `DISABLE_SERVER_SIDE_CURSORS` yoqilganini tekshiring (`DB_POOLED=1`) |

## Nega transaction pooling xavfsiz (2026-09-07 da tekshirilgan)

Transaction pooling eng ko'p foyda beradi, lekin bir nechta narsani buzadi.
Hammasi tekshirildi:

- **Server-side kursorlar** — kodda `.iterator()` yo'q, ustiga
  `DISABLE_SERVER_SIDE_CURSORS` yoqiladi.
- **Advisory lock, LISTEN/NOTIFY** — ishlatilmaydi.
- **Prepared statement'lar** — psycopg2 ularni o'zi ishlatmaydi (psycopg3 dan
  farqli). Loyihada psycopg2.
- **`SET search_path`** — `apps/shared/raw/db.py` da bor, lekin faqat
  `push_schema_context()` chaqirilganda ishlaydi, u esa **hech qayerdan
  chaqirilmaydi**. Funksiyaning ustiga ogohlantirish yozib qo'yildi: agar
  kelajakda kerak bo'lsa, `SET` ni so'rovi bilan bitta `transaction.atomic()`
  ichiga oling — aks holda schema boshqa clientga oqib ketadi.
