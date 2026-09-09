# LiveKit — Weel B2B jonli qo'ng'iroq media serveri

Jitsi (`../jitsi/`) o'rnini bosadi. Farqi: LiveKit tayyor ekran bermaydi —
video oqimlari oddiy widget bo'lib keladi, ekran esa butunlay bizniki
(dashboard'da `CallOverlay`, telefonda `CallPage`). Backend uchun qaysi
server ishlatilayotgani `CALL_PROVIDER` bilan tanlanadi; ikkalasi ham
sozlangan bo'lsa LiveKit ustun.

## Dokploy'da o'rnatish

1. DNS: `call.weel.uz` → server IP — Jitsi'dan qolgan, sertifikati ham bor. Jitsi
   compose ilovasi TO'XTATILGAN bo'lishi shart: bitta host'ga ikki router bo'lmaydi.
2. Dokploy → weel / production → *Create Service → Compose*, nom `livekit`,
   Compose Type **Docker Compose**. Manba: shu repo, Compose path
   `livekit/docker-compose.yml`, Watch path `livekit/**`.
3. Environment: `livekit/.env.example` mazmuni, `CHANGE_ME` → `openssl rand -hex 32`.
4. Deploy. Tekshiruv: `curl https://call.weel.uz` → `OK`.
5. Host'da 7881/tcp va 7882/udp tashqaridan ochiq bo'lsin. Docker e'lon qilgan
   portlar bu serverda ufw'dan o'tadi (Jitsi'ning 10000/udp'si kabi).

## Backend'ni ulash

```
CALL_PROVIDER=livekit
LIVEKIT_URL=wss://call.weel.uz
LIVEKIT_API_KEY=weel                 # = LIVEKIT_API_KEY (stack)
LIVEKIT_API_SECRET=<o'sha secret>    # = LIVEKIT_API_SECRET (stack)
CALL_GUEST_BASE_URL=https://business.weel.uz   # mijozga SMS bilan ketadigan havola shu yerda ochiladi
```

Jitsi o'zgaruvchilari qolaveradi — `CALL_PROVIDER=jitsi` bilan orqaga qaytish mumkin.

## Webhook — xonadan chiqish qo'ng'iroqni yopadi (2026-09-09)

`LIVEKIT_CONFIG`dagi `webhook:` bo'limi xona hodisalarini backend'ga POST
qiladi (`LIVEKIT_WEBHOOK_URL`, standart — dev.weel.uz). Backend
(`calls.livekit_event`) `participant_left`da xodim xonadan chiqsa qo'ng'iroqni
haqiqiy davomiyligi bilan **ENDED** qiladi va ikkala tomonga socket orqali
aytadi; `room_finished` — zaxira. Shu tufayli telefon `/end` yubora olmasa ham
(tarmoq ketgan, ilova o'ldirilgan) yozuv `accepted`da qolib ketmaydi va
tarixda soxta «5 daqiqa» chiqmaydi. Imzo — LiveKit'ning o'z JWT'si
(`Authorization`, `sha256` claim), o'sha `LIVEKIT_API_KEY/SECRET` bilan.

Tekshiruv (compose qayta deploy qilingach): ikki telefon gaplashib, biri
ilovani o'ldirsin — 20–30 soniyada `b2b_call` qatori `ended` bo'lishi va
ikkinchi telefon ekrani yopilishi kerak. Backend logida
`POST /api/b2b/workspace/calls/livekit-webhook/ 200` ko'rinadi; 401 bo'lsa —
kalit juftligi stack va backend'da farq qiladi.

## Tokenni tekshirish

Backend tokeni LiveKit'ning o'z formati: HS256 JWT, `iss` = API kaliti,
`sub` = kim, `video.room` = qaysi xona. Yolg'on secret bilan imzolangan
token `wss://call.weel.uz/rtc?access_token=…` da 401 oladi.
