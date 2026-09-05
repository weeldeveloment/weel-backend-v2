# Weel monitoring stack

Grafana · Prometheus · Loki · Tempo · Alertmanager · Alloy + exporterlar +
**alert-relay** (Alertmanager → Claude Routine) + **mcp-grafana** (Claude → Grafana).

Dokploy'da **alohida Compose ilova** sifatida, backend bilan **bitta Docker
tarmog'ida** (`dokploy-network`) ishlaydi. Tashqariga faqat `grafana.weel.uz` va
`mcp.weel.uz` chiqadi (Traefik, HTTPS). Hech bir exporter host portiga bog'lanmaydi.

```
monitoring/
├─ docker-compose.yml            butun stack (16 servis)
├─ .env.example                  → Dokploy Environment (real qiymatlar hech qachon git'da emas)
├─ prometheus/  prometheus.yml   scrape config (SHABLON — entrypoint.sh env'dan render qiladi)
│               rules/*.rules.yml  56 ta alert (backend, celery, postgres, redis, host, containers, blackbox, self)
├─ alertmanager/ alertmanager.yml  route/inhibit/receivers (SHABLON), templates/telegram.tmpl (o'zbekcha)
├─ alert-relay/  relay.py          Alertmanager webhook → Claude Routine "fire" (stdlib, build kerak emas)
├─ ops-agent/    ops.py            Claude uchun Ops API: kuzatish + cheklangan tuzatish + Telegram (/ops/, stdlib)
├─ loki/        loki.yml, rules/fake/logs.rules.yml   log asosidagi 9 ta alert (LogQL)
├─ alloy/       config.alloy       docker.sock → barcha konteyner loglari → Loki (JSON/celery/uvicorn parse)
├─ tempo/       tempo.yml          OTLP 4318/4317, span-metrics + service-graph → Prometheus
├─ blackbox/    blackbox.yml, targets.yml   tashqi HTTP probalar (domenlar shu yerda)
├─ grafana/     provisioning/{datasources,dashboards}   dashboards/*.json (13 ta, generatsiya)
├─ tools/       gen_dashboards.py  dashboard generatori;  check.sh  barcha configlarni tekshiradi
├─ RUNBOOK.md                      bulutdagi Claude routine har alert uchun shuni o'qiydi
└─ .mcp.json.example               lokal Claude Code → Grafana MCP
```

Alert oqimi (bitta pipeline, hammasi kodda):

```
Prometheus rules ─┐                        ┌─► Telegram (bot, guruh)
                  ├─► Alertmanager ─ route ─┤
Loki ruler (LogQL)┘   (inhibit, group)     └─► alert-relay ─► Claude Routine /fire ─► ops-agent (tekshir + TUZAT) ─► Telegram (hisobot)
```

---

## 0. Oldindan kerak bo'ladigan narsalar

| Nima | Qayerdan |
|---|---|
| Telegram bot token | @BotFather → `/newbot` |
| Telegram chat id | botni guruhga qo'shing, guruhga yozing, `https://api.telegram.org/bot<TOKEN>/getUpdates` → `"chat":{"id":-100...}` |
| DNS | `grafana.weel.uz`, `mcp.weel.uz` → Dokploy server IP (A yozuv) |
| Backend Dokploy app nomi | Dokploy → weel-backend ilovasi → General → **App Name** (masalan `weel-backend-v2-a1b2c3`) |
| Postgres / Redis hostlari | Dokploy'dagi DB servislarining App Name'i (dokploy-network ichida DNS) |
| Tasodifiy sirlar | `openssl rand -hex 32` × 3 (PROMETHEUS_METRICS_TOKEN, FRONTEND_LOG_TOKEN, MCP_PATH_SECRET) |

VPS resurs: stack ~1.5–2 GB RAM, disk ~10 GB (Prometheus 8 GB limit + Loki 30 kun + Tempo 14 kun).
Kam bo'lsa: `PROM_RETENTION=15d`, `PROM_RETENTION_SIZE=4GB`, loki.yml `retention_period: 360h`, tempo.yml `block_retention: 168h`.

---

## 1-bosqich. Stack'ni deploy qilish

1. Dokploy → **Create Service → Compose**. Provider: GitHub, repo `weeldeveloment/weel-backend-v2`,
   branch `main`, **Compose Path** `monitoring/docker-compose.yml`, Compose Type **Docker Compose** (Stack emas).
2. **Environment** bo'limiga `.env.example` dagi barcha o'zgaruvchilarni real qiymat bilan qo'ying.
   Majburiy: `GRAFANA_DOMAIN`, `MCP_DOMAIN`, `GRAFANA_ADMIN_PASSWORD`, `BACKEND_TARGET`,
   `PROMETHEUS_METRICS_TOKEN`, `DB_*`, `REDIS_ADDR`, `CELERY_BROKER_URL`, `TELEGRAM_*`,
   `GRAFANA_SA_TOKEN` (hozircha `glsa_placeholder` — 5-bosqichda almashtiriladi), `MCP_PATH_SECRET`.
3. Tarmoq: compose `dokploy-network` ni `external` deb ko'rsatadi — Dokploy'da bu tarmoq bor.
   Backend, Postgres, Redis ham shu tarmoqda bo'lishi kerak (Dokploy default shunday).
4. `blackbox/targets.yml` dagi domenlarni tekshiring (dev.weel.uz, business, admin, partners, weel.uz).
5. **Deploy**. Keyin `https://grafana.weel.uz` → admin bilan kiring →
   Connections → Data sources: Prometheus, Loki, Tempo, Alertmanager — 4 ta yashil.
   Explore → Prometheus → `up` → `weel-backend` dan boshqa hammasi `1` bo'lishi kerak.

## 2-bosqich. Backend'ni ulash

Backend ilovasi (Dokploy → weel-backend → Environment) ga qo'shing:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318
OTEL_SERVICE_NAME=weel-backend
OTEL_SERVICE_VERSION=<git sha yoki release>
OTEL_DEPLOYMENT_ENVIRONMENT=production
PROMETHEUS_ENABLED=1
PROMETHEUS_METRICS_TOKEN=<monitoring ilovasidagi PROMETHEUS_METRICS_TOKEN bilan BIR XIL>
FRONTEND_LOG_TOKEN=<openssl rand -hex 32>        # 4-bosqich uchun
```

`tempo` nomi hal bo'lishi uchun backend ham `dokploy-network` da bo'lishi shart
(Dokploy Application default shunday). Redeploy qiling.

Kod tomonida allaqachon bor (bu commitda):
- `core/middleware/metrics_guard.py` — `/metrics` faqat ichki IP yoki `Authorization: Bearer <token>` bilan; boshqalarga **404**.
- `core/celery.py` — task eventlar yoqilgan (`worker_send_task_events`), loglar JSON.
- `entrypoint.sh` — `PROMETHEUS_MULTIPROC_DIR` (uvicorn `--workers 4` bilan metrikalar to'g'ri yig'iladi).
- `core/settings.py` — prod'da har log qatori **bitta JSON** stdout'ga (Alloy parse qiladi); DEBUG'da odatdagi matn.
- `Dockerfile` — `HEALTHCHECK` (`/health/`).

Ixtiyoriy qo'shimcha himoya — Traefik'da `/metrics` ni umuman yopish: Dokploy → backend →
Advanced → Traefik config → router'ga middleware:
```yaml
http:
  middlewares:
    weel-block-metrics:
      replacePathRegex: { regex: "^/metrics(.*)", replacement: "/__blocked__" }
```
(metrics_guard bo'lgani uchun bu shart emas.)

Tekshirish (Grafana → Explore):
- Prometheus: `up{job="weel-backend"}` → `1`; `sum(rate(django_http_requests_total_by_view_transport_method_total[5m]))` > 0
- Loki: `{service="weel-backend", level="error"}` — JSON parse bo'lgan, `logger` label bor
- Tempo: Search → service `weel-backend` — jonli trafikdan 1 daqiqada trace'lar
- Celery: `celery_worker_up` = 1 (worker birinchi task eventini yuborgach)

## 3-bosqich. Alertlar → Telegram

Telegram bot token va chat id 1-bosqichda env'ga qo'yilgan. Tekshirish:

```bash
# Dokploy serverida (yoki lokal):
docker compose -p <project> exec alertmanager amtool alert add TestAlert severity=critical service=test \
  --annotation=summary="Sinov alerti" --alertmanager.url=http://localhost:9093
```
10–30 soniyada Telegram guruhga 🔴 FIRING xabar keladi (5 daqiqadan keyin ✅ RESOLVED).

Alertlar ro'yxati va nima qilish kerakligi — `RUNBOOK.md`. Qoidalar: `prometheus/rules/`, `loki/rules/fake/`.
Yangi qoida qo'shgach: `tools/check.sh` (promtool/amtool bilan tekshiradi), commit, Dokploy redeploy.

## 4-bosqich. Frontend loglari (4 web app)

| Ilova | Modul | Yo'l |
|---|---|---|
| weel-b2b | `src/lib/observability.ts` (avvaldan) | brauzer → `server.mjs /client-logs` → stdout → Alloy → Loki `{service="weel-b2b"}` |
| weel-admin | `src/lib/observability.ts` | brauzer → backend `POST /api/frontend/` → Loki `{service="weel-backend", logger="frontend", app="weel-admin"}` |
| dashboard_weel_uz | `src/lib/observability.ts` | xuddi shu, `app="dashboard_weel_uz"` |
| weel.uz | `src/shared/lib/observability.ts` | xuddi shu, `app="weel.uz"` |

Har birida `main.tsx` da `installObservabilityHooks()` chaqirilgan. Build env'ga:
```
VITE_FRONTEND_LOG_URL=https://dev.weel.uz/api/frontend/
VITE_FRONTEND_LOG_TOKEN=<backend FRONTEND_LOG_TOKEN bilan bir xil>
```
Token bo'sh bo'lsa modul hech narsa yubormaydi. Faqat `error`/`warning` yuboriladi,
daqiqasiga ≤20, takrorlar 60s ichida bir marta. Grafana: **Weel — Frontend** dashboard.

## 5-bosqich. Claude'ni ulash (Grafana MCP)

1. Grafana → Administration → Users and access → **Service accounts** → Add →
   nomi `weel-monitor`, roli **Viewer** → **Add service account token** → `glsa_...` ni nusxalang.
2. Monitoring ilovasi env: `GRAFANA_SA_TOKEN=glsa_...`, `MCP_PATH_SECRET=<hex>` → redeploy.
   MCP manzili: **`https://mcp.weel.uz/<MCP_PATH_SECRET>/mcp`** (URL'ning o'zi sir; faqat o'qish tool'lari).
   Tekshirish: `curl -s -X POST https://mcp.weel.uz/<secret>/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'` → `serverInfo` qaytadi.
3. **Lokal Claude Code**: `cp monitoring/.mcp.json.example /home/abbbose/projects/weel/.mcp.json`,
   ichidagi URL'ga real secret'ni qo'ying (fayl `.gitignore` da). Claude Code'ni qayta oching →
   "weel backend oxirgi 1 soat p95 latency qancha?" — Grafana tool'lari ishlaydi.
4. **claude.ai connector** (routine uchun): https://claude.ai/customize/connectors → Add custom connector →
   nomi `weel-grafana`, URL yuqoridagi MCP manzili, auth: yo'q (OAuth bo'sh). Ulangach chatda tool'lar ko'rinadi.

## 6-bosqich. ops-agent — Claude'ning production'dagi "qo'li"

`ops-agent` (compose'da bor, `monitoring/ops-agent/ops.py`) — bitta HTTP API:
kuzatish (alertlar, PromQL/LogQL/TraceQL, konteyner inspect/loglar, disk) + **cheklangan tuzatish**
(restart, oq ro'yxatdagi `exec`, `prune safe`, Dokploy redeploy, silence) + Telegram xabar.
Bulutdagi Claude Routine faqat shu API bilan ishlaydi — Grafana MCP connector shart emas.

- Manzil: **`https://mcp.weel.uz/ops/...`** (Traefik `PathPrefix(/ops/)`, MCP domenida — yangi DNS kerak emas).
- Auth: `Authorization: Bearer <OPS_TOKEN>`. Token: `openssl rand -hex 32` → Dokploy monitoring env `OPS_TOKEN` → redeploy.
- Ixtiyoriy: Dokploy → Settings → API/CLI → token → `DOKPLOY_URL`, `DOKPLOY_API_KEY` (shunda `/ops/redeploy` ishlaydi).
- Chegaralar: `OPS_DENY_REGEX` (default `dokploy|traefik`), soatiga `OPS_MAX_ACTIONS_PER_HOUR=12` amal;
  `exec` oq ro'yxati va SQL cheklovi kodda (`EXEC_ALLOW`, `SQL_ALLOW_RE`). Har amal Loki'da (`{container=~".*ops-agent.*"}`)
  va `GET /ops/actions` da; `OpsAgentRateLimited` alerti odamni chaqiradi.
- Tekshirish: `curl -s -H "Authorization: Bearer $OPS_TOKEN" https://mcp.weel.uz/ops/status | head -c 600`.

## 7-bosqich. Bulutdagi Claude Routine (kompyuter o'chiq bo'lsa ham TUZATADI)

**Muhim:** Routine claude.ai obuna (Pro/Max/Team) akkountida ishlaydi — API token emas.
Claude Code'da `claude /login` orqali **obuna akkounti** bilan kirilgan bo'lishi va
`ANTHROPIC_API_KEY` o'rnatilmagan bo'lishi kerak (`env | grep ANTHROPIC` bo'sh).

Routine'lar (2026-09-05 da yaratilgan, https://claude.ai/code/routines):
- `weel-ops-hourly` — har soat; alert bo'lsa RUNBOOK 3-bo'lim bo'yicha **tuzatadi**, hammasi yashil bo'lsa jim.
- `weel-ops-daily` — 09:00 Toshkent (`0 4 * * *` UTC); bir qatorli kunlik hisobot + kechagi amallar.
Promptlar: `RUNBOOK.md` 2-bo'lim. Siyosat (nima mumkin / mumkin emas): `RUNBOOK.md` 1-bo'lim.

Yoqishdan oldin (claude.ai UI, bir marta):
1. **Environment o'zgaruvchilari** — https://claude.ai/code → Environments → Default → Environment variables:
   `OPS_TOKEN=<Dokploy'dagi bilan bir xil>`, `OPS_URL=https://mcp.weel.uz/ops`.
   Tarmoq ruxsati (network access) `mcp.weel.uz` va `github.com` ga ochiq bo'lsin (Full yoki allowlist).
2. **GitHub** — routine `fix/ops-*` branch push + PR ochadi: Claude GitHub App'ida repo'ga write ruxsati bo'lsin.
3. Routine sahifasida **Enable**. Test: `Run now` → 2–3 daqiqada Telegram'da hisobot (yoki jim — hammasi yashil).
4. **Alert → darhol uyg'otish**: routine sahifasidagi "Trigger via API" (URL + token) → monitoring env
   `ROUTINE_FIRE_URL`, `ROUTINE_FIRE_TOKEN` (+ kerak bo'lsa `ROUTINE_BETA_HEADER`, `ROUTINE_FIRE_BODY_KEY`) → redeploy.
   Shundan keyin har critical/warning alert 10–30 soniyada Claude'ni uyg'otadi (relay 15 daqiqada 1 martadan oshirmaydi).
5. Test: backend konteynerini to'xtating → `BackendDown` → Telegram alert → Claude restart qiladi → hisobot.
   Kompyuterni yoping, keyingi soatlik run'ni kuting → claude.ai/code da session, Telegram'da hisobot.

---

## Tekshirish (uchma-uch)

```bash
tools/check.sh                      # promtool, amtool, loki, alloy, compose config, dashboard JSON
```
Grafana'da: Overview → 8 ta status yashil; Logs → `{service="weel-backend"}` oqimi; Traces → service graph;
Uptime → barcha probalar UP; Monitoring self → barcha targetlar `up`.

## Sirlar (rotatsiya)

`PROMETHEUS_METRICS_TOKEN` (backend + monitoring), `FRONTEND_LOG_TOKEN` (backend + 3 frontend build),
`GRAFANA_SA_TOKEN`, `MCP_PATH_SECRET` (monitoring + .mcp.json + claude.ai connector),
`TELEGRAM_BOT_TOKEN`, `ROUTINE_FIRE_TOKEN`, `GRAFANA_ADMIN_PASSWORD`. Hech biri git'da emas.
Tafsilot: `docs/SECRET_ROTATION.md`.

## Disk to'lsa

`docker system df`, `docker image prune -af`, Postgres `pg_database_size`, `logs/` (14 kun rotatsiya),
`docker volume ls` → `*prometheus-data`, `*loki-data`, `*tempo-data`. Prometheus 8 GB da o'zi to'xtaydi;
Loki/Tempo retention'ni loki.yml/tempo.yml da kamaytiring.

## Dashboard'larni o'zgartirish

Manba — `tools/gen_dashboards.py` (har panel bir qator). `python3 tools/gen_dashboards.py` → JSON'lar
yangilanadi → commit. UI'da qilingan o'zgarishlar saqlanmaydi (`allowUiUpdates: false`);
UI'da sinab ko'rib, JSON → generatorga ko'chiring. Chuqurroq tayyor dashboard kerak bo'lsa
Import ID: 1860 (Node), 14282 (cAdvisor), 9628 (Postgres), 763 (Redis), 7587 (Blackbox).
