# Jitsi Meet — Weel B2B jonli qo'ng'iroq serveri (1-bosqich)

TZ *"Weel B2B — Jonli video/audio qo'ng'iroq (Jitsi Meet integratsiyasi)"*,
10-bo'lim, 1-bosqich: **Infratuzilma**.

## Nima o'rnatiladi

| Xizmat  | Vazifasi                                              | Tashqariga |
|---------|-------------------------------------------------------|------------|
| web     | Jitsi Meet UI (SDK ulanadi; brauzer — tashqi mijoz)   | 443 (Traefik) |
| prosody | XMPP signal serveri, **JWT tekshiruvi**                | yo'q |
| jicofo  | Konferensiya fokusi                                    | yo'q |
| jvb     | Videobridge — media oqimi                              | **10000/udp** |

## Dokploy'da o'rnatish

1. Dokploy → *Create → Compose* → nom: `weel-jitsi`, Compose Type =
   **Docker Compose** (Stack emas).
2. Repozitoriy: shu backend repo, Compose path: `jitsi/docker-compose.yml`.
3. *Environment* bo'limiga `jitsi/.env.example` mazmunini nusxalab,
   `CHANGE_ME` qiymatlarni yarating:
   ```bash
   openssl rand -hex 32   # JWT_APP_SECRET
   openssl rand -hex 16   # JICOFO_AUTH_PASSWORD
   openssl rand -hex 16   # JVB_AUTH_PASSWORD
   ```
4. DNS: `call.weel.uz` → server IP (A yozuv). Traefik sertifikatni o'zi oladi.
5. Firewall/router: **UDP 10000** serverga ochiq bo'lsin. Server NAT ortida
   bo'lsa `JVB_ADVERTISE_IPS` — tashqi IP.
6. Deploy. Tekshiruv: `https://call.weel.uz` ochilishi kerak, lekin xonaga
   kirishga urinsangiz **"Authentication required"** — bu to'g'ri: tokensiz
   kirish taqiqlangan (TZ §8).

## Backend'ni ulash

`weel-backend` ilovasining Environment'iga:

```
JITSI_SERVER_URL=https://call.weel.uz
JITSI_APP_ID=weel                # = JWT_APP_ID
JITSI_APP_SECRET=<o'sha secret>  # = JWT_APP_SECRET
JITSI_TOKEN_TTL_SECONDS=7200
JITSI_GUEST_LINK_TTL_SECONDS=1800
CALL_RING_TIMEOUT_SECONDS=30
```

So'ng jadvalni yarating (idempotent):

```bash
python manage.py create_b2b_tables
```

Bu `b2b_call` jadvali va indekslarini qo'shadi; boshqa jadvallarga tegmaydi.

Celery beat `expire_ringing_calls` vazifasini (har daqiqa) avtomatik oladi —
`core/celery.py`. Worker ishlamasa ham javobsiz qo'ng'iroqlar API o'qilganda
o'zi yopiladi (`calls.settle`).

## JWT tekshiruvi (xavfsizlik testi, TZ §11.7)

```bash
# Muddati tugagan/yolg'on token bilan xonaga kirish rad etilishi kerak:
python - <<'PY'
import jwt, time
print(jwt.encode({"iss":"weel","aud":"jitsi","sub":"*","room":"x",
                  "exp":int(time.time())-10}, "noto'g'ri-secret", algorithm="HS256"))
PY
# https://call.weel.uz/x?jwt=<shu token>  → "Authentication failed"
```

## TURN (tavsiya)

Mobil operatorlarning NAT'i ko'pincha UDP'ni to'sadi. Barqarorlik uchun
alohida `coturn` (443/tcp, TLS) ko'taring va `.env`da `TURNS_HOST`,
`TURN_CREDENTIALS` (prosody bilan bir xil static-auth-secret) to'ldiring.
Ilova tomonida hech narsa o'zgarmaydi — TURN manzilini Jitsi konfiguratsiyasi
o'zi tarqatadi.

## Yangilash

`JITSI_IMAGE_VERSION` ni o'zgartirib qayta deploy. `data/` papkasidagi
konfiguratsiya birinchi ishga tushishda yaratiladi; katta versiya
o'zgarishida uni o'chirib qayta yaratish tavsiya etiladi.
