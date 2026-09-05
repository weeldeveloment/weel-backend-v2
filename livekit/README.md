# LiveKit — Weel B2B jonli qo'ng'iroq media serveri

Jitsi (`../jitsi/`) o'rnini bosadi. Farqi: LiveKit tayyor ekran bermaydi —
video oqimlari oddiy widget bo'lib keladi, ekran esa butunlay bizniki
(dashboard'da `CallOverlay`, telefonda `CallPage`). Backend uchun qaysi
server ishlatilayotgani `CALL_PROVIDER` bilan tanlanadi; ikkalasi ham
sozlangan bo'lsa LiveKit ustun.

## Dokploy'da o'rnatish

1. DNS: `live.weel.uz` → server IP (A yozuv).
2. Dokploy → weel / production → *Create Service → Compose*, nom `livekit`,
   Compose Type **Docker Compose**. Manba: shu repo, Compose path
   `livekit/docker-compose.yml`, Watch path `livekit/**`.
3. Environment: `livekit/.env.example` mazmuni, `CHANGE_ME` → `openssl rand -hex 32`.
4. Deploy. Tekshiruv: `curl https://live.weel.uz` → `OK`.
5. Host'da 7881/tcp va 7882/udp tashqaridan ochiq bo'lsin. Docker e'lon qilgan
   portlar bu serverda ufw'dan o'tadi (Jitsi'ning 10000/udp'si kabi).

## Backend'ni ulash

```
CALL_PROVIDER=livekit
LIVEKIT_URL=wss://live.weel.uz
LIVEKIT_API_KEY=weel                 # = LIVEKIT_API_KEY (stack)
LIVEKIT_API_SECRET=<o'sha secret>    # = LIVEKIT_API_SECRET (stack)
CALL_GUEST_BASE_URL=https://business.weel.uz   # mijozga SMS bilan ketadigan havola shu yerda ochiladi
```

Jitsi o'zgaruvchilari qolaveradi — `CALL_PROVIDER=jitsi` bilan orqaga qaytish mumkin.

## Tokenni tekshirish

Backend tokeni LiveKit'ning o'z formati: HS256 JWT, `iss` = API kaliti,
`sub` = kim, `video.room` = qaysi xona. Yolg'on secret bilan imzolangan
token `wss://live.weel.uz/rtc?access_token=…` da 401 oladi.
