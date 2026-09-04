# Weel monitoring — RUNBOOK

Bu faylni **bulutdagi Claude Routine** (va lokal Claude Code) alertni tahlil qilishda o'qiydi.
Har alert uchun: nima degani, qayerga qarash, ehtimoliy sabablar.
**Agent kod yoki infra'ni o'zgartirmaydi — faqat tekshiradi va hisobot yozadi.**

Grafana: `https://grafana.weel.uz`. Datasource UID'lari: `prometheus`, `loki`, `tempo`, `alertmanager`.
Dashboard UID'lari: `weel-overview`, `weel-backend`, `weel-celery`, `weel-postgres`, `weel-redis`,
`weel-host`, `weel-containers`, `weel-logs`, `weel-traces`, `weel-frontend`, `weel-uptime`, `weel-slo`, `weel-monitoring-self`.

---

## Routine prompt (claude.ai/code/routines ga nusxalash uchun)

```
Sen Weel platformasining production monitoring agentisan. Repo: weeldeveloment/weel-backend-v2,
ko'rsatma: monitoring/RUNBOOK.md (avval o'qi). Grafana MCP connector (weel-grafana) orqali:

1. Faol alertlar: alertmanager datasource / list_alert_rules. Firing bo'lganlarini RUNBOOK bo'yicha tahlil qil.
2. Oxirgi 1 soat golden signals (PromQL, datasource uid=prometheus):
   - so'rov/s:  sum(rate(django_http_requests_total_by_view_transport_method_total[5m]))
   - 5xx ulushi: sum(rate(django_http_responses_total_by_status_total{status=~"5.."}[5m])) / clamp_min(sum(rate(django_http_responses_total_by_status_total[5m])),0.001)
   - p95:       histogram_quantile(0.95, sum(rate(django_http_requests_latency_seconds_by_view_method_bucket[5m])) by (le))
   - celery:    sum(celery_worker_up), sum(rate(celery_task_failed_total[15m]))
   - host:      100*(1-avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))), 100*(1-node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes), disk %
   - uptime:    probe_success
3. Loglar (LogQL, uid=loki): sum(count_over_time({service="weel-backend", level=~"error|critical"}[1h]));
   ko'p bo'lsa {service="weel-backend", level="error"} dan oxirgi 30 qatorni o'qi, logger va xabar bo'yicha guruhla.
4. Trace'lar (uid=tempo): {status=error} va {duration > 1s} — eng ko'p uchraydigan span nomlari.
5. Xulosa qil. Normal bo'lsa: soatlik run'da HECH NARSA yozma; kunlik 09:00 run'da bitta qator
   "✅ Weel: hammasi yashil (so'rov/s X, 5xx Y%, p95 Zs, alert yo'q)".
   Muammo bo'lsa Telegram'ga O'ZBEKCHA, qisqa (<= 12 qator): nima, qachondan, kimga ta'sir,
   eng ehtimoliy sabab (RUNBOOK'dan), qaysi dashboard/so'rovda ko'rish mumkin, taklif qilinadigan qadam.
   Telegram: POST https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage
   JSON {"chat_id": "$TELEGRAM_CHAT_ID", "text": "...", "disable_web_page_preview": true}
   (token va chat id environment'dan; ularni hech qachon matnga yozma).
Hech qachon kod, config, alert yoki infra'ni o'zgartirma; faqat o'qi va xabar ber.
Agar Grafana MCP javob bermasa — shuni Telegram'ga yoz ("monitoring o'zi ishlamayapti").
```

---

## Agent qanday tekshiradi (sweep)

1. **Alertlar** — Alertmanager datasource orqali firing ro'yxati (severity, service, boshlangan vaqt).
2. **Golden signals** — yuqoridagi PromQL'lar (1 soat).
3. **Loglar** — ERROR soni, top xabarlar; `trace_id` bo'lsa Tempo'da trace.
4. **Trace'lar** — `{status = error}`, `{duration > 1s}`.
5. **Xulosa** — normal bo'lsa jim (kunlikda bir qator), aks holda o'zbekcha hisobot.

Kontekst: backend = Django + DRF + Celery, `uvicorn --workers 4`, `WEEL_ROLE=all` (web + worker + beat
bitta konteynerda; web qayta ishga tushsa worker ham). DB Postgres (PostGIS), Redis (cache + broker +
Channels). Tashqi xizmatlar: Hotelios (mehmonxona), Bookhara (avia), Eskiz (SMS), MinIO (fayl), Meta (lead ads),
Telegram botlar. Deploy: GitHub Actions → GHCR → Dokploy webhook. Migratsiyalar konteyner startida
(`entrypoint.sh`: migrate + create_b2b_tables + create_hotels_tables + create_avia_tables).

---

## Alertlar

### Backend

**BackendDown** (critical) — Prometheus `/metrics` ga ulana olmayapti.
- Containers dashboard: backend konteyner bormi, restartlar, OOM.
- Loki `{service="weel-backend"}` — o'chishdan oldingi oxirgi 50 qator: traceback? migrate xatosi? `Killed`?
- `BackendProbeDown` ham yonganmi (tashqi ham yotibdi) yoki faqat scrape (tarmoq/token muammosi)?
- Sabablar: yomon deploy (migrate/create_*_tables yiqildi), DB ulanmadi (`OperationalError`), OOM, `PROMETHEUS_METRICS_TOKEN` mos emas (unda 404 — `up`=0 lekin probe ok).

**BackendProbeDown** (critical) — `https://dev.weel.uz/health/` javob bermayapti.
- `probe_http_status_code`, `probe_duration_seconds` (timeout?); TLS alertlari.
- Backend `up`=1 bo'lsa — Traefik/DNS/sertifikat; `up`=0 bo'lsa — BackendDown ga qarang.

**BackendHigh5xxRate / BackendElevated5xx** — 5xx ko'p.
- Backend dashboard → "5xx — view bo'yicha", "Exception'lar tur bo'yicha".
- Loki `{service="weel-backend", level="error"}` → `logger` va `message` bo'yicha guruhla; `trace_id` → Tempo.
- Tempo `{status=error}` → qaysi endpoint.
- Sabablar: deploydan keyin bitta endpoint regressiyasi; tashqi xizmat (Hotelios/Bookhara/Eskiz/MinIO) timeout;
  DB'da ustun/jadval yo'q (`relation does not exist` — raw-SQL jadvallar faqat startda yaratiladi); Redis yotgan.

**BackendHighLatencyP95** — p95 > 1.5s (10 daqiqa).
- Backend → "p95 — view bo'yicha"; Postgres → DB query p95, ulanishlar; Celery backlog; Host CPU.
- Traces → `{duration > 1s}` — DB spanmi yoki tashqi HTTP span?
- Sabab ko'pincha: sekin so'rov (indeks), tashqi API sekin, host to'yingan.

**BackendTrafficDrop** — trafik 1 soat oldingidan 80% tushdi.
- Uptime dashboard: frontendlar probalari; TLS. Frontend dashboard: brauzer xatolari ko'paydimi (build buzilgan?).
- Kecha/hafta bilan solishtir: tabiiy pasayish (tunda) bo'lishi mumkin.

**BackendMigrationsUnapplied** — kod DB'dan oldinda. Deploy logida `migrate` yiqilgan. Sabab: yiqilgan migratsiya, DB huquqi.

**BackendDbErrors** — `django_db_errors_total` o'sdi. Loki `|= "OperationalError"`; Postgres ulanishlar 80%+?

### Celery

**CeleryNoWorkers** (critical) — worker eventlari yo'q.
- Backend konteyner tirikmi (worker uning ichida)? Redis tirikmi? Loki `{service="weel-backend", logger=~"celery.*"}`.
- **Diqqat:** deploydan keyin worker birinchi task eventini yubormaguncha `celery_worker_up` bo'lmaydi — 3 daqiqa kut.

**CeleryTaskFailureRate / CeleryTaskFailing** — tasklar yiqilmoqda.
- Celery dashboard → "Xatolar — task va exception bo'yicha".
- Tasklar: `payment.tasks.update_exchange_rate`, `notification.tasks.send_*_reminders`, `avia.poll_ticketing_status`,
  `hotels.poll_booking_statuses`, `hotels.sync_inventory` (tunda 03:30), `b2b.mail.sync_all_accounts` (har daqiqa),
  `b2b.integrations.sync_meta_pages`. Odatda tashqi API (Bookhara/Hotelios/IMAP/Meta) xatosi.

**CeleryQueueBacklog / RedisBrokerBacklog** — navbat o'smoqda. Worker yetmayapti yoki bitta task qotgan
(`CeleryTaskRuntimeHigh`). `CELERY_CONCURRENCY` (default 2).

**CeleryBeatSilent** — beat o'lgan (`RUN_CELERY_BEAT`), yoki broker uzilgan. Replikalar bo'lsa faqat bittasida beat bo'lishi kerak.

### PostgreSQL

**PostgresDown** (critical) — butun platforma. Containers → postgres konteyner; host disk (to'lsa PG yozolmaydi).
Exporter `DB_*` noto'g'ri bo'lsa ham shu alert — `pg_up` bilan birga backend `django_db_errors_total` ni ko'r.

**PostgresConnectionsHigh / PostgresIdleInTransaction** — leak. `pg_stat_activity` holatlari; uvicorn workers × CONN_MAX_AGE.

**PostgresLongRunningTransaction** — 5 daqiqadan uzoq. Qotgan migratsiya, katta hisobot so'rovi, lock kutish.

**PostgresDeadlocks** — o'sha daqiqadagi trace/loglar (`deadlock detected`).

**PostgresLowCacheHitRatio** — indeks/RAM. Ma'lumot uchun, shoshilinch emas.

**PostgresNoCommits** — 15 daqiqa commit yo'q — backend yoza olmayapti (beat tasklar ham yozadi, shuning uchun bu g'alati).

### Redis

**RedisDown** (critical) — cache + broker + WebSocket. Backend 5xx va Celery alertlari oqibat.
**RedisHighMemory / RedisEvictingKeys** — `maxmemory`, `maxmemory-policy`; eviction broker xabarlarini ham yo'qotadi.
**RedisRejectedConnections** — `maxclients`, ulanish leak.

### Host

**HostHighCPU / HostHighLoad** — Containers → CPU bo'yicha kim. Odatda backend (OCR/tesseract, hisobot) yoki Postgres.
**HostHighMemory / HostOOMKill** — Containers → xotira; `container_oom_events_total`. Loki'da `Killed`.
**HostDiskFillingUp / HostDiskCritical / HostDiskWillFillIn24h** — Docker image/layer (`docker system df`), Postgres, `logs/`,
Loki/Tempo/Prometheus volume'lari. 24h bashorat — eng muhim erta signal.
**HostInodesLow** — mayda fayllar (overlay). **HostClockSkew** — NTP; TLS/JWT/initData tekshiruvlari buziladi.
**HostNetworkErrors** — NIC; ma'lumot uchun.

### Konteynerlar

**ContainerRestartLoop** (critical) — Loki `{container=~"<nom>.*"}` oxirgi qatorlar, exit code. Backend bo'lsa: migrate, env, DB.
**ContainerOOMKilled** (critical) — limit yoki leak; xotira grafigi.
**ContainerHighMemory / ContainerHighCPUThrottling** — limitga yaqin.
**BackendContainerMissing** — konteyner umuman yo'q yoki nomi `BACKEND_CONTAINER_REGEX` ga mos emas.

### Uptime / TLS

**EndpointDown** (critical) — qaysi domen (`app` label). Traefik, DNS, sertifikat, frontend konteyneri.
**EndpointSlow** — backend p95 bilan solishtir.
**TLSCertExpiringSoon / TLSCertExpired** — Let's Encrypt/Traefik acme yangilanmayapti — infra egasi.

### Loglar (Loki ruler)

**BackendLogErrorSpike / BackendLogErrorStorm** — `{service="weel-backend", level="error"}` top xabarlar, logger.
**BackendNoLogs** — konteyner qotgan yoki Alloy ko'rmayapti (`Monitoring self` → Alloy sent/s).
**BackendTracebackBurst** — ushlanmagan exception; Tempo `{status=error}`.
**CeleryTaskErrorsInLogs** — Celery bo'limiga qarang.
**FrontendErrorSpike / B2BFrontendErrorSpike** — Frontend dashboard: qaysi `app`, qaysi `url`, xabar. Yangi frontend deploy? API kontrakti?
**OOMKilledInLogs** — xotira. **DbConnectionErrorsInLogs** — Postgres/Redis yotgan yoki limit.

### Monitoring o'zi

**MonitoringTargetDown** — stack komponenti; `Monitoring self` dashboard.
**AlertmanagerNotificationFailing** (critical) — Telegram token/chat id/tarmoq. Alertlar yo'qolmoqda.
**AlertRelayFailing** — `ROUTINE_FIRE_URL/TOKEN`; relay loglari.
**LokiIngestErrors / LokiDiscardedLines / AlloyNotShipping** — loglar yo'qolmoqda: Loki disk, rate limit, docker.sock.
**TempoNoTraces** — backend `OTEL_EXPORTER_OTLP_ENDPOINT` yo'q yoki Tempo yotgan.
**PrometheusRuleEvalFailing** — rule sintaksisi/metrika nomi.

---

## Ma'lum bo'lgan "muammo emas"lar (bularga xabar berilmaydi)

- Deploydan keyingi 3 daqiqada `celery_*` metrikalar yo'q (birinchi event kelguncha).
- Tungi beat tasklar (03:30 `hotels.sync_inventory`, 00:05 `sync_trip_statuses`) paytida qisqa p95 sakrashi.
- 4xx (401/403/404/429) alert emas — auth va validatsiya xatolari normal trafik.
- `dashboard.weel.uz`, `pms.weel.uz` — route yo'q (404 kutiladi); blackbox targets'da yo'q.
- Backend restart annotatsiyasi (ko'k chiziq) — bu deploy, alert emas.
