# Weel monitoring — RUNBOOK (SRE agent)

Bu faylni **bulutdagi Claude Routine** (va lokal Claude Code) alertni tahlil qilishda va
**tuzatishda** o'qiydi. Agent — Weel'ning avtomatik SRE'si: muammoni topadi, shu fayldagi
ruxsat doirasida **o'zi tuzatadi**, natijani tekshiradi va Telegram'ga hisobot yozadi.
Kompyuter o'chiq bo'lsa ham ishlaydi — hamma amal `ops-agent` (VPS'dagi API) orqali.

Grafana: `https://grafana.weel.uz`. Datasource UID'lari: `prometheus`, `loki`, `tempo`, `alertmanager`.
Dashboard UID'lari: `weel-overview`, `weel-backend`, `weel-celery`, `weel-postgres`, `weel-redis`,
`weel-host`, `weel-containers`, `weel-logs`, `weel-traces`, `weel-frontend`, `weel-uptime`, `weel-slo`, `weel-monitoring-self`.

---

## 0. Ops API (agentning qo'li)

Bazaviy URL: `$OPS_URL` (default `https://grafana.weel.uz/ops`; `https://mcp.weel.uz/ops` ham ishlaydi, DNS bo'lsa). Har so'rovga
`Authorization: Bearer $OPS_TOKEN` va `X-Actor: <routine nomi>` header'i.
To'liq ro'yxat: `GET /ops/help`. Manba: `monitoring/ops-agent/ops.py`.

| So'rov | Nima qiladi |
|---|---|
| `GET /ops/status` | **avval shuni ol**: faol alertlar + golden signals + muhim konteynerlar + oxirgi amallar |
| `GET /ops/alerts` | Alertmanager'dagi firing alertlar |
| `GET /ops/containers`, `GET /ops/container?name=<nom\|regex>` | ro'yxat / inspect + stats (restartlar, OOM, health, xotira) |
| `GET /ops/logs?name=<nom\|regex>&tail=200&since=<sek>&grep=<regex>` | konteyner loglari (matn) |
| `GET /ops/disk` | `docker system df` (image/volume/build cache) |
| `POST /ops/query {"ds":"prometheus\|loki\|tempo","q":"...","start"?,"end"?,"step"?,"limit"?}` | PromQL / LogQL / TraceQL |
| `GET /ops/dokploy/apps` | Dokploy ilovalari (id, nom, status) — redeploy uchun |
| `GET /ops/actions` | oxirgi 200 amal (audit) — **har amaldan oldin qara** |
| `POST /ops/restart {"name","reason"}` | konteynerni restart (dokploy/traefik'ga tegib bo'lmaydi) |
| `POST /ops/exec {"name","cmd","reason"}` | oq ro'yxatdagi buyruq (pastda) |
| `POST /ops/prune {"what":"safe"}` | 24 soatdan eski ishlatilmayotgan image + build cache |
| `POST /ops/redeploy {"kind":"application\|compose","id","reason"}` | Dokploy redeploy (agar DOKPLOY_API_KEY sozlangan bo'lsa) |
| `POST /ops/silence {"alertname","hours","comment"}` | Alertmanager'da alertni vaqtincha jimlash (≤ 4 soat) |
| `POST /ops/notify {"text","silent"?}` | Telegram guruhga xabar (bot token kerak emas) |

`exec` oq ro'yxati (server tomonda tekshiriladi, boshqasi 403):
backend → `python manage.py check|showmigrations|migrate|create_b2b_tables|create_hotels_tables|create_avia_tables|collectstatic --noinput`,
`celery -A core inspect ...`, `celery -A core purge -f`, `df -h`, `du -sh`, `ls`, `cat`, `find`, `env`;
postgres → `psql -U <user> -d <db> -c "SELECT ..."` (faqat SELECT/EXPLAIN/`pg_terminate_backend`/`pg_cancel_backend`), `pg_isready`;
redis → `redis-cli info|ping|dbsize|config get|client list|memory|slowlog|llen|scan|keys|del <celery/_kombu/unacked kalit>`.
Shell metabelgilar (`; | & > $`) ruxsat etilmagan. `migrate --fake`/`zero` yo'q.

Limitlar: soatiga **12** o'zgartiruvchi amal (restart / migrate / terminate / purge / prune / redeploy).
Tugasa API `429` qaytaradi va `OpsAgentRateLimited` alerti odamni chaqiradi.

Sinov: `curl -s -H "Authorization: Bearer $OPS_TOKEN" $OPS_URL/status | head -c 800`.

---

## 1. Siyosat — nima MUMKIN, nima MUMKIN EMAS

**Mumkin (odam so'ramasdan, darhol):**
1. Diagnostika: status, loglar, query, inspect — cheksiz.
2. `weel-backend`, `weel-b2b`, `redis`, monitoring komponentlari (`grafana|prometheus|loki|tempo|alloy|alertmanager|*exporter|celery-exporter|mcp-grafana|alert-relay`) konteynerlarini **restart** — bir konteyner uchun **soatda ko'pi bilan 2 marta**.
3. `python manage.py migrate` — faqat `BackendMigrationsUnapplied` yoki loglarda `relation "..." does not exist` / `column ... does not exist` bo'lsa. `create_*_tables` — raw-SQL jadval yo'q bo'lsa.
4. Disk: `prune safe` (HostDisk* alertlarida). Keyin `GET /ops/disk` bilan natijani ko'r.
5. Postgres: `pg_terminate_backend` — faqat `idle in transaction` > 30 daqiqa yoki `PostgresLongRunningTransaction` bo'lib so'rov **migratsiya/VACUUM emas** va > 30 daqiqa bo'lsa. Avval `pg_stat_activity` dan `pid, state, query_start, left(query,120)` ni ol va hisobotga yoz.
6. Celery: `celery inspect ping/active/reserved` diagnostika. `purge` — **faqat** navbat 10 000+ va tasklar `sync_all_accounts`/`poll_*` kabi idempotent takroriy tasklar bo'lsa; `payment.*`/`notification.*` bo'lsa **purge yo'q**.
7. Redeploy (Dokploy) — konteyner restart loop'da bo'lib restart yordam bermasa, **bir incident'da 1 marta**.
8. Silence — takroriy uyg'otishni to'xtatish uchun, ≤ 4 soat, faqat sabab aniq va odamga yozilgan bo'lsa.
9. Kod tuzatish — sabab aniq kodda (traceback fayl:qator ko'rsatadi) bo'lsa: `fix/ops-YYYYMMDD-<slug>` branch, minimal patch, testlar (`pytest` bo'lsa), push, `gh pr create`. **main'ga push YO'Q, merge YO'Q** — PR havolasini Telegram'ga yoz; odam merge qiladi, CI/CD deploy qiladi.

**Mumkin emas (hech qachon):**
- DB ma'lumotini o'zgartirish (INSERT/UPDATE/DELETE/DROP/ALTER), backup/volume o'chirish, `prune all` (Dokploy rollback image'lari ketadi).
- Sog'lom Postgres'ni restart qilish. Postgres `down` bo'lsa: restart **1 marta** mumkin, keyin odam.
- Sirlar/env/Dokploy sozlamalarini o'zgartirish, alert qoidalarini bo'shashtirish yoki o'chirish, 4 soatdan uzoq silence.
- Bir soatda bir xil amalni 2 martadan ko'p takrorlash (halqa). `GET /ops/actions` da shu ko'rinsa — **to'xta va odamga yoz**.
- `dokploy`, `traefik` konteynerlariga tegish (API o'zi 403 beradi).
- Boshqa loyihalar konteynerlariga tegish (nomida `weel` yo'q bo'lsa, tegma).

**Har amal uchun tartib:** sabab → amal (reason bilan) → `sleep 90` → qayta tekshir (`/status`, tegishli metrika/log) →
hisobotda **oldin/keyin** raqam bilan yoz. Yaxshilanmasa — keyingi pog'onaga o'tma, odamga yoz.

**Escalation (odam kerak) xabari** shunday boshlanadi: `🆘 ODAM KERAK —` va nima sinab ko'rilgani, nima ishlamagani, taklif.

---

## 2. Routine prompt (claude.ai/code/routines — soatlik va alert uchun)

```
Sen Weel platformasining production SRE agentisan: muammoni topasan VA monitoring/RUNBOOK.md dagi ruxsat
doirasida O'ZING TUZATASAN. Repo klonlangan (weeldeveloment/weel-backend-v2). ENG AVVAL monitoring/RUNBOOK.md
ni to'liq o'qi — siyosat (1-bo'lim), Ops API (0-bo'lim) va har alert uchun tuzatish yo'li (3-bo'lim) u yerda.
Prompt bilan RUNBOOK ziddiyatda bo'lsa RUNBOOK'dagi TAQIQLAR ustun.

Ops API: OPS_URL env (bo'sh bo'lsa https://grafana.weel.uz/ops), token OPS_TOKEN env'da
(header: Authorization: Bearer $OPS_TOKEN, X-Actor: weel-ops-hourly). curl bilan ishla. Token bo'sh bo'lsa —
hech narsa qilma, sessiya oxirida "OPS_TOKEN yo'q" deb yoz va tugat.
Telegram: POST $OPS_URL/notify {"text":"..."} — bot token kerak emas.

Tartib:
1. GET $OPS_URL/status — alertlar, golden signals, konteynerlar, oxirgi amallar (recent_actions).
2. Xabaringda "alert yondi" matni bo'lsa — shu alertlardan boshla; bo'lmasa bu odatiy soatlik tekshiruv.
3. Har firing alert uchun RUNBOOK 3-bo'limdagi bandni o'qi, diagnostika qil (logs/query/container), sababni aniqla.
4. RUNBOOK ruxsat bergan amalni bajar (reason maydonini to'ldir). AVVAL GET $OPS_URL/actions: shu amal oxirgi
   1 soatda 2 marta qilingan bo'lsa TAKRORLAMA — "🆘 ODAM KERAK" xabari yoz.
5. Amaldan keyin `sleep 90`, /status va tegishli metrikani qayta ol. Oldin/keyin raqam bilan yoz.
6. Sabab kodda bo'lsa (traceback aniq fayl:qator): fix/ops-YYYYMMDD-<slug> branch'da minimal patch, testlar
   (mumkin bo'lsa), push, `gh pr create` — PR havolasini Telegram'ga yoz. main'ga push QILMA, merge QILMA.
7. Hisobot Telegram'ga O'ZBEKCHA, <= 15 qator: nima bo'ldi, qachondan, kimga ta'sir, sabab, NIMA QILINDI,
   natija (oldin→keyin), keyingi qadam / odam kerakmi. Hammasi yashil va hech amal qilinmagan bo'lsa —
   HECH NARSA yozma (jim tugat).
Taqiqlar (qisqa): DB ma'lumotini o'zgartirish; sirlar/env; prune all; sog'lom Postgres restart; alert qoidalarini
yopish; 4 soatdan uzoq silence; bir konteynerni soatda 2+ restart; weel bo'lmagan konteynerlar. Shubha — qilma, yoz.
Ops API javob bermasa (timeout/5xx): 30 soniyadan keyin 2 marta qayta urin; baribir bo'lmasa /notify orqali
"🆘 monitoring/ops-agent ishlamayapti" yoz (u ham ishlamasa — natijani sessiyaga yozib tugat).
```

**Kunlik (09:00 Toshkent) routine prompt** — yuqoridagi matn + oxiriga:

```
BU KUNLIK HISOBOT: hammasi yashil bo'lsa ham BITTA qisqa xabar yoz:
"📊 Weel kunlik: so'rov/s X · 5xx Y% · p95 Zs · CPU/RAM/disk A/B/C% · faol alert: N · kechagi amallar: M
(GET /actions dan sanab, muhimlarini bir qatorda) · eng ko'p xato loggeri: ...". X-Actor: weel-ops-daily.
```

---

## 3. Alertlar — diagnostika va AVTOMATIK TUZATISH

Har band: nima degani → qayerga qarash → **Tuzatish** (ruxsat etilgan amal; bo'lmasa "odam").

Kontekst: backend = Django + DRF + Celery, `uvicorn --workers 4`, `WEEL_ROLE=all` (web + worker + beat
bitta konteynerda; web qayta ishga tushsa worker ham). DB Postgres (PostGIS), Redis (cache + broker +
Channels). Tashqi xizmatlar: Hotelios (mehmonxona), Bookhara (avia), Eskiz (SMS), MinIO (fayl), Meta (lead ads),
Telegram botlar. Deploy: GitHub Actions → GHCR → Dokploy webhook. Migratsiyalar konteyner startida
(`entrypoint.sh`: migrate + create_b2b_tables + create_hotels_tables + create_avia_tables).
Konteyner nomlari Dokploy'da `weel-backend-<id>`, `weel-postgres-<id>`, `weel-redis-<id>` ko'rinishida —
`GET /ops/containers` bilan aniqla, regex bilan murojaat qil (`weel-backend`).

### Backend

**BackendDown** (critical) — Prometheus `/metrics` ga ulana olmayapti.
- `GET /container?name=weel-backend` (state, restart_count, oom_killed, exit_code, health), `GET /logs?name=weel-backend&tail=100`.
- `BackendProbeDown` ham yonganmi (tashqi ham yotibdi) yoki faqat scrape (token muammosi → 404, probe ok)?
- Sabablar: yomon deploy (migrate/create_*_tables yiqildi), DB ulanmadi (`OperationalError`), OOM, token mos emas.
- **Tuzatish:** (a) konteyner `exited`/`restarting`, logda `OperationalError`/`Killed` → Postgres/Redis tirikmi tekshir, tirik bo'lsa `POST /restart weel-backend`; (b) logda migratsiya xatosi (`relation does not exist`, `DuplicateColumn`) → `exec showmigrations`, so'ng `exec migrate` / `create_*_tables`, keyin restart; (c) OOM → restart + hisobotga xotira grafigini yoz (limit odam ishi); (d) konteyner yo'q → `GET /dokploy/apps` → `POST /redeploy`; (e) faqat scrape (probe ok) → token mos emas, **odam**.

**BackendProbeDown** (critical) — `https://dev.weel.uz/health/` javob bermayapti.
- `probe_http_status_code`, `probe_duration_seconds`; backend `up`=1 bo'lsa — Traefik/DNS/sertifikat.
- **Tuzatish:** `up`=0 → BackendDown yo'li. `up`=1 → Traefik/DNS — **odam** (traefik'ga tegilmaydi), lekin `GET /logs?name=traefik` o'qib sababni yoz (o'qish mumkin, restart yo'q).

**BackendHigh5xxRate / BackendElevated5xx** — 5xx ko'p.
- Loki `{service="weel-backend", level="error"}` → `logger` va `message` bo'yicha guruhla; Tempo `{status=error}` → endpoint.
- **Tuzatish:** (a) `relation/column does not exist` → `exec migrate`/`create_*_tables`; (b) Redis/DB ulanish xatolari → o'sha xizmatni tekshir, Redis yotgan bo'lsa restart; (c) bitta endpoint'da aniq traceback → **kod fix PR**; (d) tashqi API (Hotelios/Bookhara/Eskiz/MinIO) timeout → tuzatib bo'lmaydi, hisobot; (e) deploydan keyin boshlangan va sabab noaniq → hisobot + **odam** (rollback odam qarori).

**BackendHighLatencyP95** — p95 > 1.5s (10 daqiqa).
- Traces `{duration > 1s}` — DB spanmi yoki tashqi HTTP? Postgres ulanishlar, Celery backlog, Host CPU.
- **Tuzatish:** `pg_stat_activity` da > 30 daqiqalik bloklovchi so'rov (migratsiya/VACUUM emas) → `pg_terminate_backend`; host CPU 95%+ va sabab `weel-backend` bo'lib worker task qotgan → restart (1 marta). Aks holda hisobot (indeks/kod — PR taklifi).

**BackendTrafficDrop** — trafik 80% tushdi. Uptime probalari, frontend xatolari, vaqt (tun). **Tuzatish:** yo'q — tahlil va hisobot; frontend probasi down bo'lsa EndpointDown yo'li.

**BackendMigrationsUnapplied** — kod DB'dan oldinda. **Tuzatish:** `exec showmigrations` (qaysilari `[ ]`), `exec migrate`, keyin `exec python manage.py check`; xato bo'lsa to'liq chiqishni hisobotga qo'y, **odam**.

**BackendDbErrors** — `django_db_errors_total` o'sdi. Loki `|= "OperationalError"`; Postgres ulanishlar. **Tuzatish:** `too many connections` → idle-in-transaction'larni terminate + backend restart; Postgres down → PostgresDown yo'li.

### Celery

**CeleryNoWorkers** (critical) — worker eventlari yo'q.
- Backend konteyner tirikmi? Redis tirikmi? `exec celery -A core inspect ping`. Deploydan keyin 3 daqiqa kut.
- **Tuzatish:** ping javob bermasa va konteyner tirik → `POST /restart weel-backend`; Redis yotgan → Redis restart. Deploydan < 5 daqiqa o'tgan bo'lsa kut, amal qilma.

**CeleryTaskFailureRate / CeleryTaskFailing** — tasklar yiqilmoqda. Odatda tashqi API (Bookhara/Hotelios/IMAP/Meta). **Tuzatish:** kod xatosi aniq bo'lsa PR; tashqi bo'lsa hisobot; DB jadval yo'q → `create_*_tables`.

**CeleryQueueBacklog / RedisBrokerBacklog** — navbat o'smoqda. `exec celery -A core inspect active` — qotgan task? **Tuzatish:** worker qotgan (active'da bitta task > 30 daqiqa) → backend restart; navbat 10 000+ va faqat idempotent poll/sync tasklar → `purge -f` (hisobotda aniq yoz); aks holda **odam**.

**CeleryBeatSilent** — beat o'lgan yoki broker uzilgan. **Tuzatish:** Redis tirik bo'lsa backend restart (1 marta).

### PostgreSQL

**PostgresDown** (critical) — butun platforma. `GET /container?name=weel-postgres` — state, oom, exit_code; host disk to'lganmi (`node_filesystem_avail_bytes`).
- **Tuzatish:** disk to'lgan → `prune safe` avval; konteyner `exited` → **1 marta** restart; qayta yiqilsa **odam** (`🆘`). Sog'lom bo'lsa (exporter `DB_*` xato) → tegma, hisobot.

**PostgresConnectionsHigh / PostgresIdleInTransaction** — leak. `psql -c "SELECT pid, state, now()-xact_start AS age, left(query,100) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY xact_start"`.
- **Tuzatish:** `idle in transaction` > 30 daqiqa → `pg_terminate_backend(pid)` (har birini hisobotga yoz); ulanishlar 90%+ va backend sababchi → backend restart.

**PostgresLongRunningTransaction** — > 5 daqiqa. **Tuzatish:** > 30 daqiqa va so'rov `SELECT`/hisobot (migratsiya, `CREATE INDEX`, `VACUUM`, `ALTER` emas) → `pg_cancel_backend`, so'ng kerak bo'lsa `pg_terminate_backend`. Migratsiya bo'lsa **tegma**.

**PostgresDeadlocks** — loglar (`deadlock detected`). **Tuzatish:** yo'q, hisobot (kod tartibi).
**PostgresLowCacheHitRatio** — ma'lumot uchun, jim (kunlikda eslat).
**PostgresNoCommits** — 15 daqiqa commit yo'q. Backend yoza olmayapti → BackendDbErrors yo'li.

### Redis

**RedisDown** (critical). **Tuzatish:** konteyner `exited` → restart (1 marta), keyin backend restart (ulanishlar tiklansin). OOM bo'lsa hisobot (maxmemory odam ishi).
**RedisHighMemory / RedisEvictingKeys** — `exec redis-cli info memory`, `config get maxmemory*`. **Tuzatish:** celery backlog sababchi bo'lsa CeleryQueueBacklog yo'li; aks holda hisobot.
**RedisRejectedConnections** — `client list` bo'yicha kim ko'p. **Tuzatish:** backend ulanish leak → backend restart.

### Host

**HostHighCPU / HostHighLoad** — `/container` stats bilan kim yeyapti. **Tuzatish:** `weel-backend` 200%+ 15 daqiqa va logda qotgan task/OCR → restart (1 marta); boshqa loyiha konteyneri bo'lsa **tegma**, hisobot.
**HostHighMemory / HostOOMKill** — `container_oom_events_total`, Loki `Killed`. **Tuzatish:** OOM bo'lgan weel konteynerini restart (o'zi restart bo'lmagan bo'lsa); hisobotda xotira grafigi.
**HostDiskFillingUp / HostDiskCritical / HostDiskWillFillIn24h** — `GET /disk`. **Tuzatish:** `POST /prune {"what":"safe"}` → `GET /disk` oldin/keyin; yetmasa `exec du -sh /app/logs` (backend), Loki/Tempo volume hajmlarini hisobotga yoz, **odam** (retention).
**HostInodesLow**, **HostClockSkew**, **HostNetworkErrors** — hisobot, amal yo'q.

### Konteynerlar

**ContainerRestartLoop** (critical) — `GET /logs?name=<nom>&tail=150`, exit code. **Tuzatish:** weel konteyneri: sabab env/DB → shu bo'limlar; sabab noaniq → `POST /redeploy` (1 marta); baribir loop → **odam**. Weel bo'lmagan konteyner → hisobot faqat.
**ContainerOOMKilled** (critical) — restart (agar o'zi ko'tarilmagan bo'lsa) + hisobot.
**ContainerHighMemory / ContainerHighCPUThrottling** — hisobot.
**BackendContainerMissing** — `GET /containers` da yo'q → `GET /dokploy/apps` → `POST /redeploy`; Dokploy sozlanmagan bo'lsa **odam**.

### Uptime / TLS

**EndpointDown** (critical) — qaysi domen (`app`). Backend bo'lsa BackendProbeDown; frontend konteyneri `exited` bo'lsa restart (weel-b2b va boshqa `weel-*` frontendlar). Traefik/DNS/sertifikat → **odam**.
**EndpointSlow** — backend p95 bilan solishtir, hisobot.
**TLSCertExpiringSoon / TLSCertExpired** — **odam** (Traefik acme).

### Loglar (Loki ruler)

**BackendLogErrorSpike / BackendLogErrorStorm** — top xabarlar, logger → tegishli bo'lim (DB/Redis/kod/tashqi).
**BackendNoLogs** — konteyner qotgan (health `unhealthy`, CPU 0, so'rovlar yo'q) → restart; Alloy ko'rmayapti → `alloy` restart.
**BackendTracebackBurst** — traceback fayl:qator → **kod fix PR**; migratsiya → migrate.
**CeleryTaskErrorsInLogs** — Celery bo'limi.
**FrontendErrorSpike / B2BFrontendErrorSpike** — qaysi `app`, `url`, xabar. Backend 5xx sababchi bo'lsa shu yo'l; frontend build → hisobot (frontend repolari bu routine'da yo'q).
**OOMKilledInLogs** — xotira bo'limi. **DbConnectionErrorsInLogs** — Postgres/Redis bo'limlari.

### Monitoring o'zi

**MonitoringTargetDown** — komponent: `GET /container?name=<komponent>` → `exited`/`unhealthy` bo'lsa restart.
**AlertmanagerNotificationFailing** (critical) — Telegram token/chat id/tarmoq. `GET /logs?name=alertmanager`. Tarmoq bo'lsa alertmanager restart (1 marta); token bo'lsa **odam**.
**AlertRelayFailing** — `ROUTINE_FIRE_URL/TOKEN`; relay loglari → **odam** (env).
**OpsAgentDown / OpsAgentActionsFailing / OpsAgentRateLimited** — agentning o'zi. Rate-limit bo'lsa demak avtomatika yetmadi → `🆘`.
**LokiIngestErrors / LokiDiscardedLines / AlloyNotShipping** — Loki disk/limit/docker.sock → alloy restart; loki restart (1 marta).
**TempoNoTraces** — backend `OTEL_EXPORTER_OTLP_ENDPOINT` yo'q yoki Tempo yotgan → tempo `exited` bo'lsa restart.
**PrometheusRuleEvalFailing** — qoida sintaksisi → **kod fix PR** (`monitoring/prometheus/rules`).

---

## 4. Ma'lum bo'lgan "muammo emas"lar (bularga xabar berilmaydi, amal qilinmaydi)

- Deploydan keyingi 3 daqiqada `celery_*` metrikalar yo'q (birinchi event kelguncha).
- Tungi beat tasklar (03:30 `hotels.sync_inventory`, 00:05 `sync_trip_statuses`) paytida qisqa p95 sakrashi.
- 4xx (401/403/404/429) alert emas — auth va validatsiya xatolari normal trafik.
- `dashboard.weel.uz`, `pms.weel.uz` — route yo'q (404 kutiladi); blackbox targets'da yo'q.
- Backend restart annotatsiyasi (ko'k chiziq) — bu deploy, alert emas.
- VPS'da boshqa loyihalar konteynerlari ham bor (`chindan`, `protouch`, `notiq`, ...) — ular Weel emas, tegilmaydi.
