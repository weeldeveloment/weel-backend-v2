#!/usr/bin/env python3
"""
Grafana dashboard'larini generatsiya qiladi -> ../grafana/dashboards/*.json

Nega generator: 13 ta dashboard, 150+ panel. Qo'lda yozilgan JSON'ni saqlab
bo'lmaydi; bu yerda har bir panel bir qator. O'zgartirish: shu faylni tahrirlang,
`python3 tools/gen_dashboards.py` ni ishga tushiring, JSON'larni commit qiling.
Grafana 30 soniyada qayta o'qiydi (provisioning updateIntervalSeconds).
"""
from __future__ import annotations

import json
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "grafana", "dashboards")
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}

# ───────────────────────────── helpers ─────────────────────────────

_pid = [0]


def _next_id() -> int:
    _pid[0] += 1
    return _pid[0]


class Grid:
    """24 ustunli gridga panellarni ketma-ket joylaydi."""

    def __init__(self):
        self.x = 0
        self.y = 0
        self.row_h = 0
        self.panels: list[dict] = []

    def add(self, panel: dict, w: int, h: int):
        if self.x + w > 24:
            self.x = 0
            self.y += self.row_h
            self.row_h = 0
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        panel["id"] = _next_id()
        self.x += w
        self.row_h = max(self.row_h, h)
        self.panels.append(panel)
        return panel

    def row(self, title: str):
        if self.x:
            self.x = 0
            self.y += self.row_h
            self.row_h = 0
        p = {"type": "row", "title": title, "collapsed": False, "panels": [], "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "id": _next_id()}
        self.y += 1
        self.panels.append(p)
        return p


def T(expr: str, legend: str = "", ds=PROM, ref: str = "A", **extra) -> dict:
    t = {"refId": ref, "datasource": ds, "expr": expr, "legendFormat": legend or "__auto"}
    if ds is PROM:
        t["range"] = True
    elif ds is LOKI:
        # $__range bo'yicha bitta son kerak bo'lsa instant so'rov arzon va aniq.
        t["queryType"] = "instant" if "$__range" in expr else "range"
    t.update(extra)
    return t


def targets(*ts) -> list:
    out = []
    for i, t in enumerate(ts):
        t = dict(t)
        t["refId"] = chr(65 + i)
        out.append(t)
    return out


def _thresholds(steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}


def timeseries(title, ts, unit=None, desc=None, ds=PROM, stack=False, fill=10, thresholds=None, min_=None, max_=None, legend="bottom", overrides=None, draw="line", color_mode=None, decimals=None):
    fc = {"defaults": {"custom": {"drawStyle": draw, "lineWidth": 1, "fillOpacity": fill, "showPoints": "never", "spanNulls": True, "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": overrides or []}
    if unit:
        fc["defaults"]["unit"] = unit
    if decimals is not None:
        fc["defaults"]["decimals"] = decimals
    if min_ is not None:
        fc["defaults"]["min"] = min_
    if max_ is not None:
        fc["defaults"]["max"] = max_
    if color_mode:
        fc["defaults"]["color"] = {"mode": color_mode}
    if thresholds:
        fc["defaults"]["thresholds"] = _thresholds(thresholds)
        fc["defaults"]["custom"]["thresholdsStyle"] = {"mode": "line"}
    return {"type": "timeseries", "title": title, "description": desc or "", "datasource": ds, "targets": targets(*ts), "fieldConfig": fc,
            "options": {"legend": {"displayMode": "list", "placement": legend, "showLegend": legend != "hidden", "calcs": []}, "tooltip": {"mode": "multi", "sort": "desc"}}}


def stat(title, ts, unit=None, desc=None, ds=PROM, thresholds=None, decimals=None, mode="value", graph="none", color="value", mappings=None, calc="lastNotNull"):
    fc = {"defaults": {"thresholds": _thresholds(thresholds or [(None, "green")])}, "overrides": []}
    if unit:
        fc["defaults"]["unit"] = unit
    if decimals is not None:
        fc["defaults"]["decimals"] = decimals
    if mappings:
        fc["defaults"]["mappings"] = mappings
    return {"type": "stat", "title": title, "description": desc or "", "datasource": ds, "targets": targets(*ts), "fieldConfig": fc,
            "options": {"reduceOptions": {"calcs": [calc], "fields": "", "values": False}, "orientation": "auto", "textMode": mode, "colorMode": color, "graphMode": graph, "justifyMode": "auto", "wideLayout": True}}


def gauge(title, ts, unit="percent", thresholds=None, desc=None, min_=0, max_=100):
    return {"type": "gauge", "title": title, "description": desc or "", "datasource": PROM, "targets": targets(*ts),
            "fieldConfig": {"defaults": {"unit": unit, "min": min_, "max": max_, "thresholds": _thresholds(thresholds or [(None, "green"), (70, "yellow"), (90, "red")])}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "showThresholdLabels": False, "showThresholdMarkers": True}}


def table(title, ts, desc=None, ds=PROM, transformations=None, overrides=None, unit=None, sort_by=None):
    fc = {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True}}, "overrides": overrides or []}
    if unit:
        fc["defaults"]["unit"] = unit
    p = {"type": "table", "title": title, "description": desc or "", "datasource": ds, "targets": targets(*ts), "fieldConfig": fc,
         "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}, "transformations": transformations or []}
    if sort_by:
        p["options"]["sortBy"] = [{"displayName": sort_by, "desc": True}]
    return p


def logs(title, expr, desc=None):
    return {"type": "logs", "title": title, "description": desc or "", "datasource": LOKI,
            "targets": [{"refId": "A", "datasource": LOKI, "expr": expr, "queryType": "range"}],
            "options": {"showTime": True, "wrapLogMessage": True, "prettifyLogMessage": False, "enableLogDetails": True, "dedupStrategy": "none", "sortOrder": "Descending", "enableInfiniteScrolling": True}}


def text(title, md, h=None):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": md}}


def alertlist(title="Yonib turgan alertlar"):
    """Prometheus ALERTS metrikasi — Alertmanager alertlist paneliga bog'liq emas, doim ishlaydi."""
    return table(title, [T('ALERTS{alertstate="firing"}', "", instant=True, range=False, format="table")],
                 transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True, "Value": True, "__name__": True, "alertstate": True, "env": True, "monitor": True, "job": True}, "indexByName": {"alertname": 0, "severity": 1, "service": 2, "instance": 3, "name": 4}}}],
                 overrides=[{"matcher": {"id": "byName", "options": "severity"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": [{"type": "value", "options": {"critical": {"color": "red", "index": 0}, "warning": {"color": "orange", "index": 1}, "info": {"color": "blue", "index": 2}}}]}]}],
                 sort_by="severity")


def var_query(name, query, label=None, ds=PROM, multi=True, include_all=True, regex=""):
    return {"type": "query", "name": name, "label": label or name, "datasource": ds, "query": {"query": query, "refId": "var"},
            "definition": query, "refresh": 2, "multi": multi, "includeAll": include_all, "allValue": ".*",
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]} if include_all else {}, "sort": 1, "regex": regex, "hide": 0}


def var_text(name, default="", label=None):
    return {"type": "textbox", "name": name, "label": label or name, "query": default, "current": {"text": default, "value": default}, "hide": 0}


def var_custom(name, options, label=None, default=None):
    default = default or options[0]
    return {"type": "custom", "name": name, "label": label or name, "query": ",".join(options), "options": [{"text": o, "value": o, "selected": o == default} for o in options], "current": {"text": default, "value": default}, "multi": False, "includeAll": False, "hide": 0}


def dashboard(uid, title, grid: Grid, tags=(), variables=(), refresh="30s", time_from="now-6h", desc=""):
    return {
        "uid": uid, "title": title, "description": desc, "tags": ["weel", *tags], "timezone": "browser", "editable": True, "graphTooltip": 1,
        "schemaVersion": 39, "version": 1, "refresh": refresh, "time": {"from": time_from, "to": "now"},
        "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m", "1h"]},
        "templating": {"list": list(variables)},
        "links": [{"title": "Weel dashboardlari", "type": "dashboards", "tags": ["weel"], "asDropdown": True, "includeVars": False, "keepTime": True, "icon": "external link"}],
        "annotations": {"list": [
            {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
            {"name": "Alertlar (Prometheus)", "datasource": PROM, "enable": True, "iconColor": "red", "expr": "ALERTS{alertstate=\"firing\", severity=~\"critical|warning\"}", "titleFormat": "{{alertname}}", "textFormat": "{{service}} {{severity}}", "step": "1m"},
            {"name": "Backend deploy (restart)", "datasource": PROM, "enable": True, "iconColor": "blue", "expr": "changes(container_start_time_seconds{name=~\".*weel[-_]?backend.*\"}[2m]) > 0", "titleFormat": "backend restart", "textFormat": "{{name}}", "step": "1m"},
        ]},
        "panels": grid.panels,
    }


def write(d: dict, filename: str):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, filename), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote", filename, "-", len([p for p in d["panels"] if p["type"] != "row"]), "panels")


# ─────────────────────── umumiy so'rovlar ───────────────────────
REQ_RATE = 'sum(rate(django_http_requests_total_by_view_transport_method_total[$__rate_interval]))'
RESP_RATE = 'sum(rate(django_http_responses_total_by_status_total[$__rate_interval]))'
ERR_RATIO = 'sum(rate(django_http_responses_total_by_status_total{status=~"5.."}[$__rate_interval])) / clamp_min(sum(rate(django_http_responses_total_by_status_total[$__rate_interval])), 0.001)'
LAT_BUCKET = 'django_http_requests_latency_seconds_by_view_method_bucket'


def q_latency(q, by="", extra=""):
    by_clause = f"by (le{', ' + by if by else ''})"
    return f'histogram_quantile({q}, sum(rate({LAT_BUCKET}{{{extra}}}[$__rate_interval])) {by_clause})'


PCT_THRESH = [(None, "green"), (70, "yellow"), (90, "red")]
ERR_THRESH = [(None, "green"), (0.01, "yellow"), (0.05, "red")]
LAT_THRESH = [(None, "green"), (0.5, "yellow"), (1.5, "red")]
UP_MAP = [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]
UP_THRESH = [(None, "red"), (1, "green")]

# ═══════════════════════ 00 Platform Overview ═══════════════════════
g = Grid()
g.add(stat("Backend", [T('min(up{job="weel-backend"})')], mappings=UP_MAP, thresholds=UP_THRESH, mode="value"), 3, 4)
g.add(stat("Postgres", [T('min(pg_up)')], mappings=UP_MAP, thresholds=UP_THRESH), 3, 4)
g.add(stat("Redis", [T('min(redis_up)')], mappings=UP_MAP, thresholds=UP_THRESH), 3, 4)
g.add(stat("Celery workerlar", [T('sum(celery_worker_up) or vector(0)')], thresholds=[(None, "red"), (1, "green")]), 3, 4)
g.add(stat("Tashqi probalar", [T('sum(probe_success) / count(probe_success)')], unit="percentunit", thresholds=[(None, "red"), (0.99, "yellow"), (1, "green")], decimals=0), 3, 4)
g.add(stat("Firing alertlar", [T('count(ALERTS{alertstate="firing", severity=~"critical|warning"}) or vector(0)')], thresholds=[(None, "green"), (1, "yellow"), (3, "red")]), 3, 4)
g.add(stat("Critical", [T('count(ALERTS{alertstate="firing", severity="critical"}) or vector(0)')], thresholds=[(None, "green"), (1, "red")]), 3, 4)
g.add(stat("Uzoq davom (backend)", [T('time() - max(process_start_time_seconds{job="weel-backend"})')], unit="dtdurations", thresholds=[(None, "yellow"), (3600, "green")]), 3, 4)

g.add(stat("So'rov/s", [T(REQ_RATE)], unit="reqps", graph="area", decimals=1), 4, 4)
g.add(stat("5xx ulushi (5m)", [T(ERR_RATIO)], unit="percentunit", thresholds=ERR_THRESH, decimals=2, graph="area"), 4, 4)
g.add(stat("p95 latency", [T(q_latency(0.95))], unit="s", thresholds=LAT_THRESH, decimals=2, graph="area"), 4, 4)
g.add(stat("Host CPU", [T('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))')], unit="percent", thresholds=PCT_THRESH, decimals=0, graph="area"), 4, 4)
g.add(stat("Host RAM", [T('100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)')], unit="percent", thresholds=PCT_THRESH, decimals=0, graph="area"), 4, 4)
g.add(stat("Disk (root)", [T('max(100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))')], unit="percent", thresholds=[(None, "green"), (80, "yellow"), (90, "red")], decimals=0, graph="area"), 4, 4)

g.add(timeseries("So'rovlar (status klassi bo'yicha)", [T('sum by (class) (label_replace(rate(django_http_responses_total_by_status_total[$__rate_interval]), "class", "${1}xx", "status", "(\\\\d)\\\\d\\\\d"))', "{{class}}")], unit="reqps", stack=True, fill=30,
                 overrides=[{"matcher": {"id": "byName", "options": "5xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}, {"matcher": {"id": "byName", "options": "2xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}, {"matcher": {"id": "byName", "options": "4xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}]}]), 12, 8)
g.add(timeseries("Latency p50 / p95 / p99", [T(q_latency(0.5), "p50"), T(q_latency(0.95), "p95"), T(q_latency(0.99), "p99")], unit="s", thresholds=[(None, "transparent"), (1.5, "red")]), 12, 8)
g.add(timeseries("5xx ulushi", [T(ERR_RATIO, "5xx %")], unit="percentunit", max_=None, thresholds=[(None, "transparent"), (0.05, "red")], color_mode="fixed"), 8, 7)
g.add(timeseries("Celery: qabul / muvaffaqiyat / xato (1/s)", [T('sum(rate(celery_task_received_total[$__rate_interval]))', "received"), T('sum(rate(celery_task_succeeded_total[$__rate_interval]))', "succeeded"), T('sum(rate(celery_task_failed_total[$__rate_interval]))', "failed")], unit="ops",
                 overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]), 8, 7)
g.add(timeseries("Host CPU / RAM / Disk %", [T('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))', "CPU"), T('100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)', "RAM"), T('max(100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))', "Disk /")], unit="percent", min_=0, max_=100), 8, 7)
g.add(alertlist(), 8, 10)
g.add(logs("Backend: oxirgi ERROR loglar", '{service="weel-backend", level=~"error|critical"}'), 16, 10)
write(dashboard("weel-overview", "Weel — Platform Overview", g, tags=["overview"], desc="Golden signals: trafik, xatolar, latency, saturation. Boshlang'ich sahifa."), "00-platform-overview.json")

# ═══════════════════════ 10 Backend (Django) ═══════════════════════
g = Grid()
V = 'view=~"$view"'
g.add(stat("So'rov/s", [T(f'sum(rate(django_http_requests_total_by_view_transport_method_total{{{V}}}[$__rate_interval]))')], unit="reqps", decimals=1, graph="area"), 4, 4)
g.add(stat("5xx / 5m", [T(f'sum(increase(django_http_responses_total_by_status_view_method_total{{status=~"5..", {V}}}[5m]))')], thresholds=[(None, "green"), (1, "yellow"), (20, "red")], decimals=0), 4, 4)
g.add(stat("4xx / 5m", [T(f'sum(increase(django_http_responses_total_by_status_view_method_total{{status=~"4..", {V}}}[5m]))')], decimals=0, color="none"), 4, 4)
g.add(stat("p95", [T(q_latency(0.95, extra=V))], unit="s", thresholds=LAT_THRESH, decimals=2), 4, 4)
g.add(stat("Ochiq DB ulanish/s", [T('sum(rate(django_db_new_connections_total[$__rate_interval]))')], unit="ops", decimals=1, color="none"), 4, 4)
g.add(stat("Qo'llanilmagan migratsiya", [T('max(django_migrations_unapplied_total) or vector(0)')], thresholds=[(None, "green"), (1, "red")]), 4, 4)

g.row("HTTP")
g.add(timeseries("So'rov/s — view bo'yicha (top 15)", [T(f'topk(15, sum by (view) (rate(django_http_requests_latency_seconds_by_view_method_count{{{V}}}[$__rate_interval])))', "{{view}}")], unit="reqps"), 12, 8)
g.add(timeseries("p95 latency — view bo'yicha (top 15)", [T(f'topk(15, {q_latency(0.95, "view", V)})', "{{view}}")], unit="s"), 12, 8)
g.add(timeseries("Javoblar status bo'yicha", [T(f'sum by (status) (rate(django_http_responses_total_by_status_view_method_total{{{V}}}[$__rate_interval]))', "{{status}}")], unit="reqps", stack=True, fill=30), 8, 8)
g.add(timeseries("5xx — view bo'yicha", [T(f'topk(10, sum by (view) (rate(django_http_responses_total_by_status_view_method_total{{status=~"5..", {V}}}[$__rate_interval])))', "{{view}}")], unit="reqps"), 8, 8)
g.add(timeseries("Exception'lar tur bo'yicha", [T('topk(10, sum by (type) (rate(django_http_exceptions_total_by_type_total[$__rate_interval])))', "{{type}}")], unit="ops"), 8, 8)
g.add(table("Endpointlar jadvali (so'rov/s, p95, 5xx/s)", [
    T(f'sum by (view) (rate(django_http_requests_latency_seconds_by_view_method_count{{{V}}}[$__range]))', "", instant=True, range=False, format="table"),
    T(f'histogram_quantile(0.95, sum by (le, view) (rate({LAT_BUCKET}{{{V}}}[$__range])))', "", instant=True, range=False, format="table"),
    T(f'sum by (view) (rate(django_http_responses_total_by_status_view_method_total{{status=~"5..", {V}}}[$__range]))', "", instant=True, range=False, format="table"),
], transformations=[{"id": "joinByField", "options": {"byField": "view", "mode": "outer"}}, {"id": "organize", "options": {"excludeByName": {"Time 1": True, "Time 2": True, "Time 3": True, "Time": True}, "renameByName": {"Value #A": "req/s", "Value #B": "p95 (s)", "Value #C": "5xx/s"}}}],
    overrides=[{"matcher": {"id": "byName", "options": "p95 (s)"}, "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 3}, {"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds(LAT_THRESH)}]},
              {"matcher": {"id": "byName", "options": "req/s"}, "properties": [{"id": "decimals", "value": 2}]},
              {"matcher": {"id": "byName", "options": "5xx/s"}, "properties": [{"id": "decimals", "value": 3}, {"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds([(None, "green"), (0.001, "red")])}]}],
    sort_by="req/s"), 24, 10)
g.add(timeseries("So'rov hajmi (body) p95 / javob hajmi p95", [T('histogram_quantile(0.95, sum(rate(django_http_requests_body_total_bytes_bucket[$__rate_interval])) by (le))', "request p95"), T('histogram_quantile(0.95, sum(rate(django_http_responses_body_total_bytes_bucket[$__rate_interval])) by (le))', "response p95")], unit="bytes"), 8, 7)
g.add(timeseries("Middleware bilan latency p95 vs view", [T('histogram_quantile(0.95, sum(rate(django_http_requests_latency_including_middlewares_seconds_bucket[$__rate_interval])) by (le))', "incl. middleware p95"), T(q_latency(0.95), "view p95")], unit="s"), 8, 7)
g.add(timeseries("Method bo'yicha", [T('sum by (method) (rate(django_http_requests_total_by_method_total[$__rate_interval]))', "{{method}}")], unit="reqps", stack=True), 8, 7)

g.row("Ma'lumotlar bazasi va cache (ilova ko'zi bilan)")
g.add(timeseries("DB so'rov latency p50 / p95 / p99", [T('histogram_quantile(0.5, sum(rate(django_db_query_duration_seconds_bucket[$__rate_interval])) by (le))', "p50"), T('histogram_quantile(0.95, sum(rate(django_db_query_duration_seconds_bucket[$__rate_interval])) by (le))', "p95"), T('histogram_quantile(0.99, sum(rate(django_db_query_duration_seconds_bucket[$__rate_interval])) by (le))', "p99")], unit="s"), 8, 7)
g.add(timeseries("DB: execute/s, yangi ulanish/s, xato/s", [T('sum(rate(django_db_execute_total[$__rate_interval]))', "execute"), T('sum(rate(django_db_new_connections_total[$__rate_interval]))', "new conn"), T('sum(rate(django_db_errors_total[$__rate_interval]))', "errors"), T('sum(rate(django_db_new_connection_errors_total[$__rate_interval]))', "conn errors")], unit="ops",
                 overrides=[{"matcher": {"id": "byRegexp", "options": ".*errors"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]), 8, 7)
g.add(timeseries("Cache hit / miss / fail (1/s)", [T('sum(rate(django_cache_get_hits_total[$__rate_interval]))', "hit"), T('sum(rate(django_cache_get_misses_total[$__rate_interval]))', "miss"), T('sum(rate(django_cache_get_fail_total[$__rate_interval]))', "fail")], unit="ops", stack=True), 8, 7)
g.add(timeseries("Cache hit ratio", [T('sum(rate(django_cache_get_hits_total[$__rate_interval])) / clamp_min(sum(rate(django_cache_get_total[$__rate_interval])), 0.001)', "hit ratio")], unit="percentunit", min_=0, max_=1), 8, 7)
g.add(timeseries("Model operatsiyalari (insert/update/delete /s)", [T('sum(rate(django_model_inserts_total[$__rate_interval]))', "insert"), T('sum(rate(django_model_updates_total[$__rate_interval]))', "update"), T('sum(rate(django_model_deletes_total[$__rate_interval]))', "delete")], unit="ops"), 8, 7)
g.add(timeseries("Jarayon: RSS xotira / ochiq fayllar", [T('sum(process_resident_memory_bytes{job="weel-backend"})', "RSS"), T('sum(process_open_fds{job="weel-backend"})', "open fds")], overrides=[{"matcher": {"id": "byName", "options": "RSS"}, "properties": [{"id": "unit", "value": "bytes"}]}]), 8, 7)

g.row("Loglar va trace'lar")
g.add(logs("ERROR loglar (backend)", '{service="weel-backend", level=~"error|critical"} | logger !~ "frontend"'), 12, 10)
g.add({"type": "table", "title": "Sekin trace'lar (> 1s, Tempo)", "datasource": TEMPO, "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "query": '{resource.service.name="weel-backend" && duration > 1s}', "limit": 20, "tableType": "traces"}], "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}, 12, 10)
write(dashboard("weel-backend", "Weel — Backend (Django)", g, tags=["backend"], variables=[var_query("view", 'label_values(django_http_requests_latency_seconds_by_view_method_count, view)', "View")]), "10-backend-django.json")

# ═══════════════════════ 20 Celery ═══════════════════════
g = Grid()
N = 'name=~"$task"'
g.add(stat("Workerlar online", [T('sum(celery_worker_up) or vector(0)')], thresholds=[(None, "red"), (1, "green")]), 4, 4)
g.add(stat("Faol tasklar", [T('sum(celery_worker_tasks_active) or vector(0)')], color="none"), 4, 4)
g.add(stat("Navbat uzunligi", [T('sum(celery_queue_length) or max(redis_key_size{key=~"celery.*"}) or vector(0)')], thresholds=[(None, "green"), (100, "yellow"), (500, "red")]), 4, 4)
g.add(stat("Failure ulushi (15m)", [T('sum(rate(celery_task_failed_total[15m])) / clamp_min(sum(rate(celery_task_received_total[15m])), 0.001)')], unit="percentunit", thresholds=ERR_THRESH, decimals=1), 4, 4)
g.add(stat("Task/min", [T('sum(rate(celery_task_received_total[$__rate_interval])) * 60')], decimals=1, graph="area", color="none"), 4, 4)
g.add(stat("Oxirgi beat task", [T('time() - max(timestamp(changes(celery_task_received_total{name=~"stories.tasks.persist_story_views|activities.expire_stale_pending_bookings"}[5m]) > 0))')], unit="dtdurations", thresholds=[(None, "green"), (900, "yellow"), (1800, "red")], desc="Har 2-10 daqiqada ishlaydigan beat tasklar oxirgi marta qachon kelgan"), 4, 4)

g.row("Oqim")
g.add(timeseries("Task oqimi — nom bo'yicha (received/s, top 15)", [T(f'topk(15, sum by (name) (rate(celery_task_received_total{{{N}}}[$__rate_interval])))', "{{name}}")], unit="ops"), 12, 8)
g.add(timeseries("Holatlar: sent / received / started / succeeded / failed / retried", [T('sum(rate(celery_task_sent_total[$__rate_interval]))', "sent"), T('sum(rate(celery_task_received_total[$__rate_interval]))', "received"), T('sum(rate(celery_task_started_total[$__rate_interval]))', "started"), T('sum(rate(celery_task_succeeded_total[$__rate_interval]))', "succeeded"), T('sum(rate(celery_task_failed_total[$__rate_interval]))', "failed"), T('sum(rate(celery_task_retried_total[$__rate_interval]))', "retried")], unit="ops",
                 overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]), 12, 8)
g.add(timeseries("Xatolar — task va exception bo'yicha", [T(f'topk(10, sum by (name, exception) (rate(celery_task_failed_total{{{N}}}[$__rate_interval])))', "{{name}} / {{exception}}")], unit="ops"), 12, 8)
g.add(timeseries("Retry / rejected / revoked", [T('sum by (name) (rate(celery_task_retried_total[$__rate_interval]))', "retry {{name}}"), T('sum(rate(celery_task_rejected_total[$__rate_interval]))', "rejected"), T('sum(rate(celery_task_revoked_total[$__rate_interval]))', "revoked")], unit="ops"), 12, 8)

g.row("Ishlash vaqti va navbat")
g.add(timeseries("Runtime p95 — task bo'yicha (top 12)", [T(f'topk(12, histogram_quantile(0.95, sum by (le, name) (rate(celery_task_runtime_bucket{{{N}}}[$__rate_interval]))))', "{{name}}")], unit="s"), 12, 8)
g.add(timeseries("Runtime p50 / p95 / p99 (umumiy)", [T('histogram_quantile(0.5, sum(rate(celery_task_runtime_bucket[$__rate_interval])) by (le))', "p50"), T('histogram_quantile(0.95, sum(rate(celery_task_runtime_bucket[$__rate_interval])) by (le))', "p95"), T('histogram_quantile(0.99, sum(rate(celery_task_runtime_bucket[$__rate_interval])) by (le))', "p99")], unit="s"), 12, 8)
g.add(timeseries("Navbat uzunligi — navbat bo'yicha", [T('sum by (queue_name) (celery_queue_length)', "{{queue_name}}"), T('max(redis_key_size{key=~"celery.*"})', "redis list 'celery'")], thresholds=[(None, "transparent"), (500, "red")]), 8, 7)
g.add(timeseries("Worker: faol tasklar / jarayonlar", [T('sum by (hostname) (celery_worker_tasks_active)', "active {{hostname}}"), T('sum(celery_active_process_count)', "processes")]), 8, 7)
g.add(table("Task jadvali (received/s, fail/s, p95)", [
    T('sum by (name) (rate(celery_task_received_total[$__range]))', "", instant=True, range=False, format="table"),
    T('sum by (name) (rate(celery_task_failed_total[$__range]))', "", instant=True, range=False, format="table"),
    T('histogram_quantile(0.95, sum by (le, name) (rate(celery_task_runtime_bucket[$__range])))', "", instant=True, range=False, format="table"),
], transformations=[{"id": "joinByField", "options": {"byField": "name", "mode": "outer"}}, {"id": "organize", "options": {"excludeByName": {"Time 1": True, "Time 2": True, "Time 3": True, "Time": True}, "renameByName": {"Value #A": "received/s", "Value #B": "failed/s", "Value #C": "p95 (s)"}}}],
    overrides=[{"matcher": {"id": "byName", "options": "p95 (s)"}, "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 2}]}, {"matcher": {"id": "byName", "options": "failed/s"}, "properties": [{"id": "decimals", "value": 4}, {"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds([(None, "green"), (0.0001, "red")])}]}, {"matcher": {"id": "byName", "options": "received/s"}, "properties": [{"id": "decimals", "value": 4}]}],
    sort_by="received/s"), 8, 7)
g.add(logs("Celery loglari (error)", '{service="weel-backend", logger=~"celery.*|.*tasks.*", level=~"error|warning"}'), 24, 9)
write(dashboard("weel-celery", "Weel — Celery", g, tags=["celery"], variables=[var_query("task", 'label_values(celery_task_received_total, name)', "Task")]), "20-celery.json")

# ═══════════════════════ 30 PostgreSQL ═══════════════════════
g = Grid()
D = 'datname!~"template.*|postgres"'
g.add(stat("Postgres", [T('min(pg_up)')], mappings=UP_MAP, thresholds=UP_THRESH), 3, 4)
g.add(stat("Ulanishlar", [T('sum(pg_stat_activity_count)')], color="none"), 3, 4)
g.add(stat("max_connections", [T('max(pg_settings_max_connections)')], color="none"), 3, 4)
g.add(gauge("Ulanish %", [T('100 * sum(pg_stat_activity_count) / max(pg_settings_max_connections)')], thresholds=[(None, "green"), (60, "yellow"), (80, "red")]), 3, 4)
g.add(stat("Cache hit ratio", [T(f'sum(rate(pg_stat_database_blks_hit{{{D}}}[$__rate_interval])) / clamp_min(sum(rate(pg_stat_database_blks_hit{{{D}}}[$__rate_interval])) + sum(rate(pg_stat_database_blks_read{{{D}}}[$__rate_interval])), 1)')], unit="percentunit", thresholds=[(None, "red"), (0.9, "yellow"), (0.97, "green")], decimals=1), 3, 4)
g.add(stat("DB hajmi", [T(f'sum(pg_database_size_bytes{{{D}}})')], unit="bytes", color="none"), 3, 4)
g.add(stat("Deadlock (24h)", [T(f'sum(increase(pg_stat_database_deadlocks{{{D}}}[24h]))')], thresholds=[(None, "green"), (1, "red")], decimals=0), 3, 4)
g.add(stat("Uzoq davom", [T('time() - max(pg_postmaster_start_time_seconds)')], unit="dtdurations", color="none"), 3, 4)

g.add(timeseries("Ulanishlar holat bo'yicha", [T('sum by (state) (pg_stat_activity_count)', "{{state}}"), T('max(pg_settings_max_connections)', "max_connections")], stack=False, overrides=[{"matcher": {"id": "byName", "options": "max_connections"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}]), 12, 8)
g.add(timeseries("Tranzaksiyalar: commit / rollback (1/s)", [T(f'sum(rate(pg_stat_database_xact_commit{{{D}}}[$__rate_interval]))', "commit"), T(f'sum(rate(pg_stat_database_xact_rollback{{{D}}}[$__rate_interval]))', "rollback")], unit="ops", overrides=[{"matcher": {"id": "byName", "options": "rollback"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]), 12, 8)
g.add(timeseries("Qatorlar: fetched / returned / inserted / updated / deleted (1/s)", [T(f'sum(rate(pg_stat_database_tup_fetched{{{D}}}[$__rate_interval]))', "fetched"), T(f'sum(rate(pg_stat_database_tup_returned{{{D}}}[$__rate_interval]))', "returned"), T(f'sum(rate(pg_stat_database_tup_inserted{{{D}}}[$__rate_interval]))', "inserted"), T(f'sum(rate(pg_stat_database_tup_updated{{{D}}}[$__rate_interval]))', "updated"), T(f'sum(rate(pg_stat_database_tup_deleted{{{D}}}[$__rate_interval]))', "deleted")], unit="ops"), 8, 8)
g.add(timeseries("Cache hit ratio", [T(f'sum(rate(pg_stat_database_blks_hit{{{D}}}[$__rate_interval])) / clamp_min(sum(rate(pg_stat_database_blks_hit{{{D}}}[$__rate_interval])) + sum(rate(pg_stat_database_blks_read{{{D}}}[$__rate_interval])), 1)', "hit ratio")], unit="percentunit", min_=0.8, max_=1, thresholds=[(None, "transparent"), (0.95, "green")]), 8, 8)
g.add(timeseries("Deadlock / konflikt (1/s)", [T(f'sum(rate(pg_stat_database_deadlocks{{{D}}}[$__rate_interval]))', "deadlocks"), T(f'sum(rate(pg_stat_database_conflicts{{{D}}}[$__rate_interval]))', "conflicts")], unit="ops"), 8, 8)
g.add(timeseries("Eng uzoq tranzaksiya (holat bo'yicha)", [T('max by (state) (pg_stat_activity_max_tx_duration)', "{{state}}")], unit="s", thresholds=[(None, "transparent"), (300, "red")]), 8, 7)
g.add(timeseries("Locklar rejim bo'yicha", [T(f'sum by (mode) (pg_locks_count{{{D}}})', "{{mode}}")], stack=True), 8, 7)
g.add(timeseries("DB hajmi", [T(f'sum by (datname) (pg_database_size_bytes{{{D}}})', "{{datname}}")], unit="bytes"), 8, 7)
g.add(timeseries("Disk I/O: blok o'qish (1/s), temp fayllar (bytes/s)", [T(f'sum(rate(pg_stat_database_blks_read{{{D}}}[$__rate_interval]))', "blocks read"), T(f'sum(rate(pg_stat_database_temp_bytes{{{D}}}[$__rate_interval]))', "temp bytes/s")]), 8, 7)
g.add(timeseries("Checkpoint / bgwriter", [T('rate(pg_stat_bgwriter_checkpoints_timed_total[$__rate_interval])', "checkpoints timed"), T('rate(pg_stat_bgwriter_checkpoints_req_total[$__rate_interval])', "checkpoints requested"), T('rate(pg_stat_bgwriter_buffers_backend_total[$__rate_interval])', "buffers backend")], unit="ops"), 8, 7)
g.add(timeseries("Backend (ilova) ko'zi bilan: DB so'rov p95 va xatolar", [T('histogram_quantile(0.95, sum(rate(django_db_query_duration_seconds_bucket[$__rate_interval])) by (le))', "django query p95"), T('sum(rate(django_db_errors_total[$__rate_interval]))', "django db errors/s")], overrides=[{"matcher": {"id": "byName", "options": "django query p95"}, "properties": [{"id": "unit", "value": "s"}]}]), 8, 7)
write(dashboard("weel-postgres", "Weel — PostgreSQL", g, tags=["infra", "postgres"], desc="postgres-exporter + django_db_* metrikalari"), "30-postgres.json")

# ═══════════════════════ 35 Redis ═══════════════════════
g = Grid()
g.add(stat("Redis", [T('min(redis_up)')], mappings=UP_MAP, thresholds=UP_THRESH), 3, 4)
g.add(stat("Xotira", [T('sum(redis_memory_used_bytes)')], unit="bytes", color="none"), 3, 4)
g.add(stat("maxmemory", [T('sum(redis_memory_max_bytes)')], unit="bytes", color="none", desc="0 = limit yo'q"), 3, 4)
g.add(stat("Mijozlar", [T('sum(redis_connected_clients)')], color="none"), 3, 4)
g.add(stat("Ops/s", [T('sum(rate(redis_commands_processed_total[$__rate_interval]))')], unit="ops", decimals=0, graph="area", color="none"), 3, 4)
g.add(stat("Hit rate", [T('sum(rate(redis_keyspace_hits_total[$__rate_interval])) / clamp_min(sum(rate(redis_keyspace_hits_total[$__rate_interval])) + sum(rate(redis_keyspace_misses_total[$__rate_interval])), 0.001)')], unit="percentunit", thresholds=[(None, "red"), (0.5, "yellow"), (0.8, "green")], decimals=0), 3, 4)
g.add(stat("Evicted (1h)", [T('sum(increase(redis_evicted_keys_total[1h]))')], thresholds=[(None, "green"), (1, "red")], decimals=0), 3, 4)
g.add(stat("Celery navbati", [T('max(redis_key_size{key=~"celery.*"}) or vector(0)')], thresholds=[(None, "green"), (100, "yellow"), (1000, "red")]), 3, 4)

g.add(timeseries("Xotira: used / max / rss", [T('sum(redis_memory_used_bytes)', "used"), T('sum(redis_memory_max_bytes)', "max"), T('sum(redis_memory_used_rss_bytes)', "rss")], unit="bytes", overrides=[{"matcher": {"id": "byName", "options": "max"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}]), 12, 8)
g.add(timeseries("Buyruqlar/s — buyruq bo'yicha (top 12)", [T('topk(12, sum by (cmd) (rate(redis_commands_total[$__rate_interval])))', "{{cmd}}")], unit="ops", stack=True), 12, 8)
g.add(timeseries("Keyspace hit / miss", [T('sum(rate(redis_keyspace_hits_total[$__rate_interval]))', "hits"), T('sum(rate(redis_keyspace_misses_total[$__rate_interval]))', "misses")], unit="ops", stack=True), 8, 7)
g.add(timeseries("Evicted / expired kalitlar (1/s)", [T('sum(rate(redis_evicted_keys_total[$__rate_interval]))', "evicted"), T('sum(rate(redis_expired_keys_total[$__rate_interval]))', "expired")], unit="ops"), 8, 7)
g.add(timeseries("Kalitlar soni — DB bo'yicha", [T('sum by (db) (redis_db_keys)', "{{db}}")]), 8, 7)
g.add(timeseries("Celery broker navbati (Redis list uzunligi)", [T('max by (key) (redis_key_size{key=~"celery.*"})', "{{key}}")], thresholds=[(None, "transparent"), (1000, "red")]), 8, 7)
g.add(timeseries("Mijozlar: ulangan / bloklangan / rad etilgan", [T('sum(redis_connected_clients)', "connected"), T('sum(redis_blocked_clients)', "blocked"), T('sum(rate(redis_rejected_connections_total[$__rate_interval]))', "rejected/s")]), 8, 7)
g.add(timeseries("Buyruq latency (o'rtacha, ms) — top 8", [T('topk(8, 1000 * rate(redis_commands_duration_seconds_total[$__rate_interval]) / clamp_min(rate(redis_commands_total[$__rate_interval]), 0.001))', "{{cmd}}")], unit="ms"), 8, 7)
g.add(timeseries("Tarmoq I/O", [T('sum(rate(redis_net_input_bytes_total[$__rate_interval]))', "in"), T('sum(rate(redis_net_output_bytes_total[$__rate_interval]))', "out")], unit="Bps"), 12, 6)
g.add(timeseries("CPU (sys/user)", [T('rate(redis_cpu_sys_seconds_total[$__rate_interval])', "sys"), T('rate(redis_cpu_user_seconds_total[$__rate_interval])', "user")], unit="percentunit"), 12, 6)
write(dashboard("weel-redis", "Weel — Redis", g, tags=["infra", "redis"], desc="redis-exporter: cache, Celery broker, Channels layer"), "35-redis.json")

# ═══════════════════════ 40 Host ═══════════════════════
g = Grid()
FS = 'fstype!~"tmpfs|overlay|squashfs"'
g.add(stat("CPU yadro", [T('count(count by (cpu) (node_cpu_seconds_total{mode="idle"}))')], color="none"), 3, 4)
g.add(gauge("CPU %", [T('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))')]), 3, 4)
g.add(gauge("RAM %", [T('100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)')]), 3, 4)
g.add(gauge("Disk / %", [T('max(100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))')], thresholds=[(None, "green"), (80, "yellow"), (90, "red")]), 3, 4)
g.add(stat("RAM jami", [T('node_memory_MemTotal_bytes')], unit="bytes", color="none"), 3, 4)
g.add(stat("Load 1/5/15", [T('node_load1', "1m"), T('node_load5', "5m"), T('node_load15', "15m")], decimals=2, color="none", mode="value_and_name"), 3, 4)
g.add(stat("Uptime", [T('time() - node_boot_time_seconds')], unit="dtdurations", color="none"), 3, 4)
g.add(stat("Disk to'lishiga (trend, /)", [T('max((node_filesystem_avail_bytes{mountpoint="/"}) / clamp_min(-deriv(node_filesystem_avail_bytes{mountpoint="/"}[6h]), 1))')], unit="dtdurations", thresholds=[(None, "red"), (86400, "yellow"), (604800, "green")], desc="Oxirgi 6 soat sur'atida disk necha vaqtda to'ladi (o'sish bo'lmasa juda katta son)"), 3, 4)

g.row("CPU va yuk")
g.add(timeseries("CPU rejim bo'yicha", [T('100 * avg by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[$__rate_interval]))', "{{mode}}")], unit="percent", stack=True, fill=40, max_=100), 12, 8)
g.add(timeseries("Load average vs yadrolar", [T('node_load1', "load1"), T('node_load5', "load5"), T('node_load15', "load15"), T('count(count by (cpu) (node_cpu_seconds_total{mode="idle"}))', "cores")], overrides=[{"matcher": {"id": "byName", "options": "cores"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}]), 12, 8)

g.row("Xotira")
g.add(timeseries("Xotira", [T('node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes', "used"), T('node_memory_Cached_bytes + node_memory_Buffers_bytes', "cache+buffers"), T('node_memory_MemAvailable_bytes', "available"), T('node_memory_MemTotal_bytes', "total")], unit="bytes", stack=False), 12, 8)
g.add(timeseries("Swap va OOM", [T('node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes', "swap used"), T('increase(node_vmstat_oom_kill[$__rate_interval])', "oom kills")], overrides=[{"matcher": {"id": "byName", "options": "swap used"}, "properties": [{"id": "unit", "value": "bytes"}]}, {"matcher": {"id": "byName", "options": "oom kills"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.drawStyle", "value": "bars"}]}]), 12, 8)

g.row("Disk")
g.add(timeseries("Fayl tizimi band %", [T(f'100 * (1 - node_filesystem_avail_bytes{{{FS}}} / node_filesystem_size_bytes{{{FS}}})', "{{mountpoint}}")], unit="percent", min_=0, max_=100, thresholds=[(None, "transparent"), (85, "red")]), 8, 8)
g.add(timeseries("Disk I/O (bytes/s)", [T('sum by (device) (rate(node_disk_read_bytes_total{device!~"loop.*|dm-.*"}[$__rate_interval]))', "read {{device}}"), T('sum by (device) (rate(node_disk_written_bytes_total{device!~"loop.*|dm-.*"}[$__rate_interval]))', "write {{device}}")], unit="Bps"), 8, 8)
g.add(timeseries("Disk IO band (%) va kutish", [T('100 * rate(node_disk_io_time_seconds_total{device!~"loop.*|dm-.*"}[$__rate_interval])', "busy {{device}}"), T('rate(node_disk_io_time_weighted_seconds_total{device!~"loop.*|dm-.*"}[$__rate_interval])', "weighted {{device}}")], overrides=[{"matcher": {"id": "byRegexp", "options": "busy.*"}, "properties": [{"id": "unit", "value": "percent"}]}]), 8, 8)
g.add(timeseries("Inode band %", [T(f'100 * (1 - node_filesystem_files_free{{{FS}}} / node_filesystem_files{{{FS}}})', "{{mountpoint}}")], unit="percent", min_=0, max_=100), 8, 7)
g.add(timeseries("Bo'sh joy (trend 24h)", [T('node_filesystem_avail_bytes{mountpoint="/"}', "avail /"), T('predict_linear(node_filesystem_avail_bytes{mountpoint="/"}[6h], 24*3600)', "predicted +24h")], unit="bytes"), 8, 7)
g.add(timeseries("Fayl deskriptorlar / jarayonlar", [T('node_filefd_allocated', "fds"), T('node_procs_running', "running"), T('node_procs_blocked', "blocked")]), 8, 7)

g.row("Tarmoq")
g.add(timeseries("Tarmoq trafigi", [T('sum by (device) (rate(node_network_receive_bytes_total{device!~"lo|veth.*|br.*|docker.*"}[$__rate_interval]))', "rx {{device}}"), T('-sum by (device) (rate(node_network_transmit_bytes_total{device!~"lo|veth.*|br.*|docker.*"}[$__rate_interval]))', "tx {{device}}")], unit="Bps"), 12, 7)
g.add(timeseries("Tarmoq xatolar / drop, TCP ulanishlar", [T('sum(rate(node_network_receive_errs_total[$__rate_interval]) + rate(node_network_transmit_errs_total[$__rate_interval]))', "errors/s"), T('sum(rate(node_network_receive_drop_total[$__rate_interval]) + rate(node_network_transmit_drop_total[$__rate_interval]))', "drops/s"), T('node_netstat_Tcp_CurrEstab', "tcp established"), T('node_sockstat_TCP_tw', "tcp time_wait")]), 12, 7)
write(dashboard("weel-host", "Weel — Host (VPS)", g, tags=["infra", "host"], desc="node-exporter"), "40-host.json")

# ═══════════════════════ 45 Containers ═══════════════════════
g = Grid()
C = 'name=~"$container", name!=""'
g.add(stat("Konteynerlar", [T('count(container_last_seen{name!=""})')], color="none"), 4, 4)
g.add(stat("Restart (1h)", [T(f'sum(changes(container_start_time_seconds{{{C}}}[1h]))')], thresholds=[(None, "green"), (1, "yellow"), (3, "red")], decimals=0), 4, 4)
g.add(stat("OOM (24h)", [T(f'sum(increase(container_oom_events_total{{{C}}}[24h]))')], thresholds=[(None, "green"), (1, "red")], decimals=0), 4, 4)
g.add(stat("Jami CPU (yadro)", [T(f'sum(rate(container_cpu_usage_seconds_total{{{C}}}[$__rate_interval]))')], decimals=2, color="none"), 4, 4)
g.add(stat("Jami xotira", [T(f'sum(container_memory_working_set_bytes{{{C}}})')], unit="bytes", color="none"), 4, 4)
g.add(stat("Backend konteyner", [T('count(container_last_seen{name=~".*weel[-_]?backend.*"}) or vector(0)')], thresholds=[(None, "red"), (1, "green")]), 4, 4)

g.add(timeseries("CPU (yadro) — konteyner bo'yicha", [T(f'topk(15, sum by (name) (rate(container_cpu_usage_seconds_total{{{C}}}[$__rate_interval])))', "{{name}}")], stack=True, fill=30), 12, 9)
g.add(timeseries("Xotira (working set) — konteyner bo'yicha", [T(f'topk(15, sum by (name) (container_memory_working_set_bytes{{{C}}}))', "{{name}}")], unit="bytes", stack=True, fill=30), 12, 9)
g.add(timeseries("Xotira limitga nisbatan %", [T(f'100 * container_memory_working_set_bytes{{{C}}} / clamp_min(container_spec_memory_limit_bytes{{{C}}}, 1) and container_spec_memory_limit_bytes{{{C}}} < 1e15', "{{name}}")], unit="percent", max_=100, thresholds=[(None, "transparent"), (90, "red")]), 8, 8)
g.add(timeseries("CPU throttling %", [T(f'100 * rate(container_cpu_cfs_throttled_periods_total{{{C}}}[$__rate_interval]) / clamp_min(rate(container_cpu_cfs_periods_total{{{C}}}[$__rate_interval]), 1)', "{{name}}")], unit="percent", max_=100), 8, 8)
g.add(timeseries("Restartlar (15m) va OOM", [T(f'changes(container_start_time_seconds{{{C}}}[15m])', "restart {{name}}"), T(f'increase(container_oom_events_total{{{C}}}[15m])', "oom {{name}}")], draw="bars", fill=60), 8, 8)
g.add(timeseries("Tarmoq — konteyner bo'yicha (rx+tx)", [T(f'topk(10, sum by (name) (rate(container_network_receive_bytes_total{{{C}}}[$__rate_interval]) + rate(container_network_transmit_bytes_total{{{C}}}[$__rate_interval])))', "{{name}}")], unit="Bps"), 8, 7)
g.add(timeseries("Disk yozish/o'qish — konteyner bo'yicha", [T(f'topk(10, sum by (name) (rate(container_fs_writes_bytes_total{{{C}}}[$__rate_interval]) + rate(container_fs_reads_bytes_total{{{C}}}[$__rate_interval])))', "{{name}}")], unit="Bps"), 8, 7)
g.add(timeseries("Jarayonlar / threadlar — konteyner bo'yicha", [T(f'topk(10, sum by (name) (container_threads{{{C}}}))', "{{name}}")]), 8, 7)
g.add(table("Konteynerlar jadvali", [
    T('sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[5m]))', "", instant=True, range=False, format="table"),
    T('sum by (name) (container_memory_working_set_bytes{name!=""})', "", instant=True, range=False, format="table"),
    T('sum by (name) (changes(container_start_time_seconds{name!=""}[24h]))', "", instant=True, range=False, format="table"),
    T('sum by (name) (time() - container_start_time_seconds{name!=""})', "", instant=True, range=False, format="table"),
], transformations=[{"id": "joinByField", "options": {"byField": "name", "mode": "outer"}}, {"id": "organize", "options": {"excludeByName": {"Time 1": True, "Time 2": True, "Time 3": True, "Time 4": True, "Time": True}, "renameByName": {"Value #A": "CPU (cores)", "Value #B": "RAM", "Value #C": "restarts 24h", "Value #D": "uptime"}}}],
    overrides=[{"matcher": {"id": "byName", "options": "RAM"}, "properties": [{"id": "unit", "value": "bytes"}]}, {"matcher": {"id": "byName", "options": "uptime"}, "properties": [{"id": "unit", "value": "dtdurations"}]}, {"matcher": {"id": "byName", "options": "CPU (cores)"}, "properties": [{"id": "decimals", "value": 3}]}, {"matcher": {"id": "byName", "options": "restarts 24h"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds([(None, "green"), (1, "yellow"), (3, "red")])}]}],
    sort_by="RAM"), 24, 10)
write(dashboard("weel-containers", "Weel — Containers (Docker)", g, tags=["infra", "containers"], variables=[var_query("container", 'label_values(container_last_seen{name!=""}, name)', "Konteyner")], desc="cAdvisor"), "45-containers.json")

# ═══════════════════════ 50 Logs ═══════════════════════
g = Grid()
SEL = '{service=~"$service"}'
g.add(stat("Log qatorlari (diapazon)", [T(f'sum(count_over_time({SEL} [$__range]))', ds=LOKI)], ds=LOKI, color="none", decimals=0), 4, 4)
g.add(stat("ERROR (diapazon)", [T(f'sum(count_over_time({SEL} | level=~"error|critical" [$__range]))', ds=LOKI)], ds=LOKI, thresholds=[(None, "green"), (10, "yellow"), (100, "red")], decimals=0), 4, 4)
g.add(stat("WARNING (diapazon)", [T(f'sum(count_over_time({SEL} | level="warning" [$__range]))', ds=LOKI)], ds=LOKI, color="none", decimals=0), 4, 4)
g.add(stat("Traceback (diapazon)", [T(f'sum(count_over_time({SEL} |= "Traceback (most recent call last)" [$__range]))', ds=LOKI)], ds=LOKI, thresholds=[(None, "green"), (1, "yellow"), (10, "red")], decimals=0), 4, 4)
g.add(stat("Log hajmi (bytes)", [T(f'sum(bytes_over_time({SEL} [$__range]))', ds=LOKI)], ds=LOKI, unit="bytes", color="none"), 4, 4)
g.add(stat("Loki: rad etilgan qatorlar (1h)", [T('sum(increase(loki_discarded_samples_total[1h])) or vector(0)')], thresholds=[(None, "green"), (1, "yellow"), (100, "red")], decimals=0), 4, 4)

g.add(timeseries("Log hajmi — level bo'yicha", [T(f'sum by (level) (count_over_time({SEL} [$__auto]))', "{{level}}", ds=LOKI)], ds=LOKI, stack=True, fill=40, draw="bars",
                 overrides=[{"matcher": {"id": "byName", "options": "error"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}, {"matcher": {"id": "byName", "options": "warning"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}]}, {"matcher": {"id": "byName", "options": "info"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}, {"matcher": {"id": "byName", "options": "critical"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "dark-red"}}]}]), 12, 8)
g.add(timeseries("ERROR — logger bo'yicha (top 10)", [T(f'topk(10, sum by (logger) (count_over_time({SEL} | level=~"error|critical" [$__auto])))', "{{logger}}", ds=LOKI)], ds=LOKI, draw="bars", stack=True, fill=40), 12, 8)
g.add(table("Eng ko'p uchraydigan ERROR xabarlar (diapazon)", [T(f'topk(20, sum by (message) (count_over_time({SEL} | level=~"error|critical" | json | __error__="" | message != "" [$__range])))', ds=LOKI, instant=True, queryType="instant")], ds=LOKI,
              transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": {"Value": "soni", "Value #A": "soni"}}}], sort_by="soni"), 12, 9)
g.add(table("Loglar — konteyner bo'yicha (diapazon)", [T('topk(20, sum by (container, service) (count_over_time({container=~".+"} [$__range])))', ds=LOKI, instant=True, queryType="instant")], ds=LOKI,
              transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": {"Value": "qatorlar", "Value #A": "qatorlar"}}}], sort_by="qatorlar"), 12, 9)
g.add(logs("ERROR / CRITICAL oqimi", f'{SEL} | level=~"error|critical" |~ "(?i)$search"'), 24, 12)
g.add(logs("Barcha loglar (filtr: service, level, matn)", f'{SEL} | level=~"$level" |~ "(?i)$search"'), 24, 14)
write(dashboard("weel-logs", "Weel — Logs", g, tags=["logs"], variables=[
    var_custom("service", ["weel-backend", "weel-b2b", ".*"], "Service", default="weel-backend"),
    var_custom("level", [".*", "error|critical", "warning", "info", "debug"], "Level", default=".*"),
    var_text("search", "", "Matn (regex)"),
], desc="Loki. trace_id ustiga bosib Tempo'ga o'ting."), "50-logs.json")

# ═══════════════════════ 55 Traces ═══════════════════════
g = Grid()
SVC = 'service=~"$svc"'
g.add(stat("Span/s (qabul)", [T('sum(rate(tempo_distributor_spans_received_total[$__rate_interval]))')], unit="ops", decimals=1, graph="area", color="none"), 4, 4)
g.add(stat("Trace'lar (metrics-gen, 1/s)", [T(f'sum(rate(traces_spanmetrics_calls_total{{{SVC}, span_kind="SPAN_KIND_SERVER"}}[$__rate_interval]))')], unit="ops", decimals=1, color="none"), 4, 4)
g.add(stat("Xatoli span ulushi", [T(f'sum(rate(traces_spanmetrics_calls_total{{{SVC}, status_code="STATUS_CODE_ERROR"}}[$__rate_interval])) / clamp_min(sum(rate(traces_spanmetrics_calls_total{{{SVC}}}[$__rate_interval])), 0.001)')], unit="percentunit", thresholds=ERR_THRESH, decimals=2), 4, 4)
g.add(stat("Server span p95", [T(f'histogram_quantile(0.95, sum(rate(traces_spanmetrics_latency_bucket{{{SVC}, span_kind="SPAN_KIND_SERVER"}}[$__rate_interval])) by (le))')], unit="s", thresholds=LAT_THRESH, decimals=2), 4, 4)
g.add(stat("DB span p95", [T('histogram_quantile(0.95, sum(rate(traces_spanmetrics_latency_bucket{span_kind="SPAN_KIND_CLIENT", db_system!=""}[$__rate_interval])) by (le))')], unit="s", thresholds=[(None, "green"), (0.1, "yellow"), (0.5, "red")], decimals=3), 4, 4)
g.add(stat("Tempo ingest xatolari (1h)", [T('sum(increase(tempo_discarded_spans_total[1h])) or vector(0)')], thresholds=[(None, "green"), (1, "yellow"), (100, "red")], decimals=0), 4, 4)

g.add({"type": "nodeGraph", "title": "Service graph (Tempo)", "datasource": TEMPO, "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "serviceMap"}], "options": {"nodes": {"mainStatUnit": "ms"}}}, 12, 12)
g.add(timeseries("So'rov/s — span nomi bo'yicha (server, top 15)", [T(f'topk(15, sum by (span_name) (rate(traces_spanmetrics_calls_total{{{SVC}, span_kind="SPAN_KIND_SERVER"}}[$__rate_interval])))', "{{span_name}}")], unit="reqps"), 12, 6)
g.add(timeseries("p95 — span nomi bo'yicha (server, top 15)", [T(f'topk(15, histogram_quantile(0.95, sum by (le, span_name) (rate(traces_spanmetrics_latency_bucket{{{SVC}, span_kind="SPAN_KIND_SERVER"}}[$__rate_interval]))))', "{{span_name}}")], unit="s"), 12, 6)
g.add(timeseries("Xatoli spanlar — nom bo'yicha", [T(f'topk(10, sum by (span_name) (rate(traces_spanmetrics_calls_total{{{SVC}, status_code="STATUS_CODE_ERROR"}}[$__rate_interval])))', "{{span_name}}")], unit="ops"), 8, 7)
g.add(timeseries("Tashqi/DB chaqiruvlar p95 (client spanlar)", [T('topk(10, histogram_quantile(0.95, sum by (le, span_name) (rate(traces_spanmetrics_latency_bucket{span_kind="SPAN_KIND_CLIENT"}[$__rate_interval]))))', "{{span_name}}")], unit="s"), 8, 7)
g.add(timeseries("Service graph: xato ulushi", [T('sum by (client, server) (rate(traces_service_graph_request_failed_total[$__rate_interval])) / clamp_min(sum by (client, server) (rate(traces_service_graph_request_total[$__rate_interval])), 0.001)', "{{client}} → {{server}}")], unit="percentunit"), 8, 7)
g.add({"type": "table", "title": "Xatoli trace'lar", "datasource": TEMPO, "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "query": '{resource.service.name=~"$svc" && status = error}', "limit": 20, "tableType": "traces"}], "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}, 12, 9)
g.add({"type": "table", "title": "Eng sekin trace'lar (> 2s)", "datasource": TEMPO, "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "query": '{resource.service.name=~"$svc" && duration > 2s}', "limit": 20, "tableType": "traces"}], "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}, 12, 9)
write(dashboard("weel-traces", "Weel — Traces (Tempo)", g, tags=["traces"], variables=[var_query("svc", 'label_values(traces_spanmetrics_calls_total, service)', "Service")], desc="OpenTelemetry -> Tempo. Span metrics Tempo metrics-generator'dan."), "55-traces.json")

# ═══════════════════════ 60 Frontend ═══════════════════════
g = Grid()
FE = '{service="weel-backend", logger="frontend", app=~"$app"}'
B2B = '{service="weel-b2b", app=~"$app"}'
g.add(stat("Brauzer xatolari (diapazon)", [T(f'sum(count_over_time({FE} | level="error" [$__range])) + sum(count_over_time({B2B} | level="error" [$__range]))', ds=LOKI)], ds=LOKI, thresholds=[(None, "green"), (10, "yellow"), (100, "red")], decimals=0), 4, 4)
g.add(stat("Ogohlantirishlar (diapazon)", [T(f'sum(count_over_time({FE} | level="warning" [$__range])) + sum(count_over_time({B2B} | level=~"warn|warning" [$__range]))', ds=LOKI)], ds=LOKI, color="none", decimals=0), 4, 4)
g.add(stat("Frontend log so'rovlari /s (backend)", [T('sum(rate(django_http_requests_latency_seconds_by_view_method_count{view=~".*frontend.*|.*FrontendLog.*"}[$__rate_interval]))')], unit="reqps", decimals=2, color="none"), 4, 4)
g.add(stat("Rad etilgan (401/429) /5m", [T('sum(increase(django_http_responses_total_by_status_view_method_total{view=~".*frontend.*|.*FrontendLog.*", status=~"401|429|503"}[5m]))')], thresholds=[(None, "green"), (1, "yellow"), (50, "red")], decimals=0, desc="401 = token noto'g'ri, 429 = throttle, 503 = FRONTEND_LOG_TOKEN o'rnatilmagan"), 4, 4)
g.add(stat("Frontend saytlar UP", [T('sum(probe_success{kind="frontend"}) / count(probe_success{kind="frontend"})')], unit="percentunit", thresholds=[(None, "red"), (0.99, "yellow"), (1, "green")], decimals=0), 4, 4)
g.add(stat("Frontend p95 yuklanish (blackbox)", [T('max(avg_over_time(probe_duration_seconds{kind="frontend"}[5m]))')], unit="s", thresholds=[(None, "green"), (2, "yellow"), (4, "red")], decimals=2), 4, 4)

g.add(timeseries("Xatolar — ilova bo'yicha", [T(f'sum by (app) (count_over_time({FE} | level="error" [$__auto]))', "{{app}}", ds=LOKI), T(f'sum by (app) (count_over_time({B2B} | level="error" [$__auto]))', "{{app}}", ds=LOKI)], ds=LOKI, draw="bars", stack=True, fill=40), 12, 8)
g.add(timeseries("Barcha brauzer loglari — level bo'yicha", [T(f'sum by (level) (count_over_time({FE} [$__auto]))', "{{level}}", ds=LOKI), T(f'sum by (level) (count_over_time({B2B} [$__auto]))', "b2b {{level}}", ds=LOKI)], ds=LOKI, draw="bars", stack=True, fill=40), 12, 8)
g.add(table("Eng ko'p uchraydigan xato xabarlar", [T(f'topk(20, sum by (app, message) (count_over_time({FE} | level="error" | json | __error__="" [$__range])))', ds=LOKI, instant=True, queryType="instant")], ds=LOKI,
              transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": {"Value": "soni", "Value #A": "soni"}}}], sort_by="soni"), 12, 9)
g.add(table("Xato — sahifa (url) bo'yicha", [T(f'topk(20, sum by (app, url) (count_over_time({FE} | level="error" | json | __error__="" [$__range])))', ds=LOKI, instant=True, queryType="instant")], ds=LOKI,
              transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": {"Value": "soni", "Value #A": "soni"}}}], sort_by="soni"), 12, 9)
g.add(timeseries("Saytlar yuklanish vaqti (blackbox)", [T('probe_duration_seconds{kind="frontend"}', "{{app}}")], unit="s"), 12, 7)
g.add(timeseries("Saytlar UP (blackbox)", [T('probe_success{kind="frontend"}', "{{app}}")], min_=0, max_=1, draw="line", fill=20), 12, 7)
g.add(logs("Brauzer xatolari (weel-admin, dashboard_weel_uz, weel.uz)", f'{FE} | level=~"error|warning"'), 12, 12)
g.add(logs("Brauzer xatolari (weel-b2b, server.mjs orqali)", f'{B2B} | level=~"error|warn|warning"'), 12, 12)
write(dashboard("weel-frontend", "Weel — Frontend (4 web app)", g, tags=["frontend"], variables=[var_custom("app", [".*", "weel-admin", "dashboard_weel_uz", "weel.uz", "weel-b2b"], "Ilova", default=".*")], desc="Brauzer loglari: window.onerror / unhandledrejection / console.error -> backend /api/frontend/ (yoki weel-b2b server.mjs) -> Loki"), "60-frontend.json")

# ═══════════════════════ 65 Uptime ═══════════════════════
g = Grid()
g.add(stat("Probalar UP", [T('sum(probe_success)')], color="none"), 3, 4)
g.add(stat("Probalar DOWN", [T('count(probe_success == 0) or vector(0)')], thresholds=[(None, "green"), (1, "red")]), 3, 4)
g.add(stat("Availability (30d, API)", [T('avg_over_time(probe_success{app="backend"}[30d])')], unit="percentunit", decimals=3, thresholds=[(None, "red"), (0.99, "yellow"), (0.995, "green")]), 3, 4)
g.add(stat("Availability (7d, API)", [T('avg_over_time(probe_success{app="backend"}[7d])')], unit="percentunit", decimals=3, thresholds=[(None, "red"), (0.99, "yellow"), (0.995, "green")]), 3, 4)
g.add(stat("Eng yaqin TLS tugashi", [T('min((probe_ssl_earliest_cert_expiry - time()) / 86400)')], unit="d", thresholds=[(None, "red"), (14, "yellow"), (30, "green")], decimals=0), 3, 4)
g.add(stat("API proba (avg 5m)", [T('avg_over_time(probe_duration_seconds{app="backend"}[5m])')], unit="s", thresholds=[(None, "green"), (1, "yellow"), (3, "red")], decimals=2), 3, 4)
g.add(stat("Grafana probasi", [T('probe_success{app="grafana"}')], mappings=UP_MAP, thresholds=UP_THRESH), 3, 4)
g.add(stat("Blackbox exporter", [T('up{job="blackbox-exporter"} or up{job=~".*blackbox.*"} or vector(1)')], mappings=UP_MAP, thresholds=UP_THRESH), 3, 4)

g.add(table("Endpointlar holati", [
    T('probe_success', "", instant=True, range=False, format="table"),
    T('probe_http_status_code', "", instant=True, range=False, format="table"),
    T('probe_duration_seconds', "", instant=True, range=False, format="table"),
    T('(probe_ssl_earliest_cert_expiry - time()) / 86400', "", instant=True, range=False, format="table"),
    T('avg_over_time(probe_success[24h])', "", instant=True, range=False, format="table"),
], transformations=[{"id": "joinByField", "options": {"byField": "instance", "mode": "outer"}}, {"id": "organize", "options": {"excludeByName": {"Time 1": True, "Time 2": True, "Time 3": True, "Time 4": True, "Time 5": True, "Time": True, "job 1": True, "job 2": True, "job 3": True, "job 4": True, "job 5": True, "__name__": True, "app 2": True, "app 3": True, "app 4": True, "app 5": True, "kind 2": True, "kind 3": True, "kind 4": True, "kind 5": True}, "renameByName": {"Value #A": "up", "Value #B": "HTTP", "Value #C": "vaqt (s)", "Value #D": "TLS kun", "Value #E": "24h availability", "app 1": "app", "kind 1": "kind"}}}],
    overrides=[{"matcher": {"id": "byName", "options": "up"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds(UP_THRESH)}, {"id": "mappings", "value": UP_MAP}]},
              {"matcher": {"id": "byName", "options": "vaqt (s)"}, "properties": [{"id": "decimals", "value": 2}, {"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds([(None, "green"), (1, "yellow"), (3, "red")])}]},
              {"matcher": {"id": "byName", "options": "TLS kun"}, "properties": [{"id": "decimals", "value": 0}, {"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds([(None, "red"), (14, "yellow"), (30, "green")])}]},
              {"matcher": {"id": "byName", "options": "24h availability"}, "properties": [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 3}]}]), 24, 9)
g.add(timeseries("Proba muvaffaqiyati", [T('probe_success', "{{instance}}")], min_=0, max_=1, fill=20), 12, 7)
g.add(timeseries("Proba davomiyligi", [T('probe_duration_seconds', "{{instance}}")], unit="s", thresholds=[(None, "transparent"), (3, "red")]), 12, 7)
g.add(timeseries("Proba fazalari (API): DNS / connect / TLS / processing / transfer", [T('probe_http_duration_seconds{app="backend"}', "{{phase}}")], unit="s", stack=True, fill=30), 12, 7)
g.add(timeseries("TLS sertifikat qolgan kunlar", [T('(probe_ssl_earliest_cert_expiry - time()) / 86400', "{{instance}}")], unit="d", thresholds=[(None, "transparent"), (14, "red")]), 12, 7)
write(dashboard("weel-uptime", "Weel — Uptime / Blackbox", g, tags=["uptime"], desc="Tashqi HTTP probalar (blackbox/targets.yml)", time_from="now-24h"), "65-uptime.json")

# ═══════════════════════ 70 SLO ═══════════════════════
g = Grid()
TARGET = 0.995
W = "$window"
avail = f'1 - sum(increase(django_http_responses_total_by_status_total{{status=~"5.."}}[{W}])) / clamp_min(sum(increase(django_http_responses_total_by_status_total[{W}])), 1)'
lat_ok = f'sum(increase({LAT_BUCKET}{{le="1.0"}}[{W}])) / clamp_min(sum(increase(django_http_requests_latency_seconds_by_view_method_count[{W}])), 1)'
budget_left = f'1 - (1 - ({avail})) / (1 - {TARGET})'
g.add(text("SLO ta'rifi", f"**Availability SLO: {TARGET*100:.1f}%** — 5xx bo'lmagan javoblar ulushi (`$window` oynasida).\n\n**Latency SLO: 95%** so'rovlar **≤ 1s** (view latency histogrammasi).\n\n**Uptime SLO: {TARGET*100:.1f}%** — tashqi proba (`https://dev.weel.uz/health/`).\n\nError budget = ruxsat etilgan xato ulushi ({(1-TARGET)*100:.1f}%). Burn rate = hozirgi xato sur'ati / budget sur'ati; 1h burn > 14 yoki 6h burn > 6 — budget bir necha kunda tugaydi."), 6, 8)
g.add(stat(f"Availability ($window)", [T(avail)], unit="percentunit", decimals=3, thresholds=[(None, "red"), (0.99, "yellow"), (TARGET, "green")]), 6, 4)
g.add(stat("Latency SLO: ≤1s ulushi ($window)", [T(lat_ok)], unit="percentunit", decimals=2, thresholds=[(None, "red"), (0.9, "yellow"), (0.95, "green")]), 6, 4)
g.add(stat("Uptime — tashqi proba ($window)", [T(f'avg_over_time(probe_success{{app="backend"}}[{W}])')], unit="percentunit", decimals=3, thresholds=[(None, "red"), (0.99, "yellow"), (TARGET, "green")]), 6, 4)
g.add(gauge("Error budget qoldig'i ($window)", [T(f'100 * clamp_min({budget_left}, 0)')], thresholds=[(None, "red"), (25, "yellow"), (50, "green")]), 6, 4)
g.add(stat("Burn rate 1h", [T(f'(1 - (1 - sum(increase(django_http_responses_total_by_status_total{{status=~"5.."}}[1h])) / clamp_min(sum(increase(django_http_responses_total_by_status_total[1h])), 1))) / (1 - {TARGET})')], decimals=2, thresholds=[(None, "green"), (2, "yellow"), (14, "red")]), 6, 4)
g.add(stat("Burn rate 6h", [T(f'(1 - (1 - sum(increase(django_http_responses_total_by_status_total{{status=~"5.."}}[6h])) / clamp_min(sum(increase(django_http_responses_total_by_status_total[6h])), 1))) / (1 - {TARGET})')], decimals=2, thresholds=[(None, "green"), (1, "yellow"), (6, "red")]), 6, 4)
g.add(stat("5xx soni ($window)", [T(f'sum(increase(django_http_responses_total_by_status_total{{status=~"5.."}}[{W}]))')], decimals=0, color="none"), 6, 4)
g.add(timeseries("Availability (1h oynali, vaqt bo'yicha)", [T('1 - sum(rate(django_http_responses_total_by_status_total{status=~"5.."}[1h])) / clamp_min(sum(rate(django_http_responses_total_by_status_total[1h])), 0.001)', "availability 1h"), T(str(TARGET), "SLO")], unit="percentunit", min_=0.98, max_=1, overrides=[{"matcher": {"id": "byName", "options": "SLO"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}, {"id": "custom.fillOpacity", "value": 0}]}]), 12, 8)
g.add(timeseries("Burn rate (1h / 6h)", [T(f'(sum(rate(django_http_responses_total_by_status_total{{status=~"5.."}}[1h])) / clamp_min(sum(rate(django_http_responses_total_by_status_total[1h])), 0.001)) / (1 - {TARGET})', "1h"), T(f'(sum(rate(django_http_responses_total_by_status_total{{status=~"5.."}}[6h])) / clamp_min(sum(rate(django_http_responses_total_by_status_total[6h])), 0.001)) / (1 - {TARGET})', "6h")], thresholds=[(None, "transparent"), (6, "yellow"), (14, "red")]), 12, 8)
g.add(timeseries("Latency: ≤1s ulushi (1h oynali)", [T('sum(rate(django_http_requests_latency_seconds_by_view_method_bucket{le="1.0"}[1h])) / clamp_min(sum(rate(django_http_requests_latency_seconds_by_view_method_count[1h])), 0.001)', "≤1s"), T("0.95", "SLO")], unit="percentunit", min_=0.8, max_=1), 12, 8)
g.add(timeseries("Tashqi uptime (1h oynali)", [T('avg_over_time(probe_success{app="backend"}[1h])', "API"), T('avg_over_time(probe_success{kind="frontend"}[1h])', "{{app}}")], unit="percentunit", min_=0.95, max_=1), 12, 8)
write(dashboard("weel-slo", "Weel — SLO", g, tags=["slo"], variables=[var_custom("window", ["30d", "7d", "24h", "1h"], "Oyna", default="30d")], time_from="now-7d", refresh="5m"), "70-slo.json")

# ═══════════════════════ 80 Monitoring self ═══════════════════════
g = Grid()
g.add(table("Scrape targetlar", [T('up', "", instant=True, range=False, format="table"), T('scrape_duration_seconds', "", instant=True, range=False, format="table")],
              transformations=[{"id": "joinByField", "options": {"byField": "instance", "mode": "outer"}}, {"id": "organize", "options": {"excludeByName": {"Time 1": True, "Time 2": True, "Time": True, "__name__": True, "job 2": True, "service 2": True, "app 2": True, "kind 2": True}, "renameByName": {"Value #A": "up", "Value #B": "scrape (s)", "job 1": "job", "service 1": "service", "app 1": "app", "kind 1": "kind"}}}],
              overrides=[{"matcher": {"id": "byName", "options": "up"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": _thresholds(UP_THRESH)}, {"id": "mappings", "value": UP_MAP}]}, {"matcher": {"id": "byName", "options": "scrape (s)"}, "properties": [{"id": "decimals", "value": 3}]}]), 12, 10)
g.add(alertlist("Alertlar (barchasi)"), 12, 10)
g.add(timeseries("Prometheus: TSDB hajmi, series soni", [T('prometheus_tsdb_storage_blocks_bytes + prometheus_tsdb_wal_storage_size_bytes', "tsdb bytes"), T('prometheus_tsdb_head_series', "head series")], overrides=[{"matcher": {"id": "byName", "options": "tsdb bytes"}, "properties": [{"id": "unit", "value": "bytes"}]}]), 8, 7)
g.add(timeseries("Loki: qabul (bytes/s), rad etilgan qatorlar", [T('sum(rate(loki_distributor_bytes_received_total[$__rate_interval]))', "bytes/s"), T('sum by (reason) (rate(loki_discarded_samples_total[$__rate_interval]))', "discarded {{reason}}")], overrides=[{"matcher": {"id": "byName", "options": "bytes/s"}, "properties": [{"id": "unit", "value": "Bps"}]}]), 8, 7)
g.add(timeseries("Tempo: span qabul / rad", [T('sum(rate(tempo_distributor_spans_received_total[$__rate_interval]))', "received/s"), T('sum by (reason) (rate(tempo_discarded_spans_total[$__rate_interval]))', "discarded {{reason}}")], unit="ops"), 8, 7)
g.add(timeseries("Alloy -> Loki: yuborilgan qatorlar, xatolar", [T('sum(rate(loki_write_sent_entries_total[$__rate_interval]))', "sent/s"), T('sum(rate(loki_write_dropped_entries_total[$__rate_interval]))', "dropped/s"), T('sum(rate(loki_write_request_duration_seconds_count{status_code!~"2.."}[$__rate_interval]))', "failed req/s")], unit="ops"), 8, 7)
g.add(timeseries("Alertmanager: bildirishnomalar (muvaffaqiyat / xato)", [T('sum by (integration) (rate(alertmanager_notifications_total[$__rate_interval]))', "sent {{integration}}"), T('sum by (integration) (rate(alertmanager_notifications_failed_total[$__rate_interval]))', "failed {{integration}}")], unit="ops"), 8, 7)
g.add(timeseries("alert-relay: Claude Routine uyg'otishlar", [T('increase(relay_fire_total[$__rate_interval])', "fired"), T('increase(relay_fire_failed_total[$__rate_interval])', "failed"), T('increase(relay_throttled_total[$__rate_interval])', "throttled"), T('increase(relay_disabled_total[$__rate_interval])', "disabled (URL yo'q)")], draw="bars"), 8, 7)
g.add(timeseries("Monitoring konteynerlari xotirasi", [T('sum by (name) (container_memory_working_set_bytes{name=~".*(grafana|prometheus|loki|tempo|alloy|alertmanager|cadvisor|exporter|relay|mcp).*"})', "{{name}}")], unit="bytes", stack=True, fill=30), 12, 7)
g.add(timeseries("Monitoring volume'lar hajmi (Loki/Tempo/Prom)", [T('sum by (name) (container_fs_usage_bytes{name=~".*(prometheus|loki|tempo).*"})', "{{name}}")], unit="bytes"), 12, 7)
write(dashboard("weel-monitoring-self", "Weel — Monitoring (o'zini kuzatish)", g, tags=["monitoring"], desc="Stack'ning o'zi sog'lommi: targetlar, ingest, bildirishnomalar"), "80-monitoring-self.json")

print("done")
