"""
alert-relay: Alertmanager webhook  ->  Claude Code Routine "fire" API.

Alertmanager (Prometheus va Loki alertlari) firing holatdagi guruhni bu yerga
POST qiladi; biz undan qisqa o'zbekcha matn yasab, Routine'ning API triggerini
chaqiramiz. Routine (bulutda, kompyuter o'chiq bo'lsa ham) Grafana MCP orqali
holatni tekshirib, RUNBOOK.md bo'yicha tahlilni Telegram'ga yozadi.

Faqat stdlib — image: python:3.12-alpine, build kerak emas.

Env:
  ROUTINE_FIRE_URL            routine'ning "Trigger via API" URL'i (bo'sh = o'chiq, faqat log)
  ROUTINE_FIRE_TOKEN          shu trigger'ning bearer tokeni
  ROUTINE_BETA_HEADER         ixtiyoriy, masalan "anthropic-beta: experimental-cc-routine-2026-04-01"
  RELAY_MIN_INTERVAL_SECONDS  ketma-ket ikki uyg'otish orasidagi minimal vaqt (default 900)
  GRAFANA_DOMAIN              matndagi havolalar uchun
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIRE_URL = os.getenv("ROUTINE_FIRE_URL", "").strip()
FIRE_TOKEN = os.getenv("ROUTINE_FIRE_TOKEN", "").strip()
BETA_HEADER = os.getenv("ROUTINE_BETA_HEADER", "").strip()
MIN_INTERVAL = int(os.getenv("RELAY_MIN_INTERVAL_SECONDS", "900") or "900")
GRAFANA_DOMAIN = os.getenv("GRAFANA_DOMAIN", "grafana.weel.uz").strip()
PORT = int(os.getenv("RELAY_PORT", "8080"))

_lock = threading.Lock()
_state = {"last_fire_at": 0.0, "pending": None}
_counters = {
    "relay_webhooks_received_total": 0,
    "relay_fire_total": 0,
    "relay_fire_failed_total": 0,
    "relay_throttled_total": 0,
    "relay_disabled_total": 0,
}


def log(msg: str, **kv) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "msg": msg, **kv}
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def build_text(payload: dict) -> str:
    alerts = [a for a in payload.get("alerts", []) if a.get("status") == "firing"]
    if not alerts:
        return ""
    lines = ["Weel production'da alert yondi. Grafana MCP orqali tekshirib, RUNBOOK.md bo'yicha tahlil qil va Telegram'ga o'zbekcha qisqa hisobot yoz.", ""]
    for a in alerts[:10]:
        labels = a.get("labels", {})
        ann = a.get("annotations", {})
        lines.append(
            f"- {labels.get('alertname', '?')} [{labels.get('severity', '?')}/{labels.get('service', '?')}]"
            f"{' ' + labels['instance'] if labels.get('instance') else ''}"
            f"{' ' + labels['name'] if labels.get('name') else ''}"
            f" — {ann.get('summary', '')}"
            f"{' — ' + ann['description'] if ann.get('description') else ''}"
            f" (boshlandi: {a.get('startsAt', '')[:19]})"
        )
        if ann.get("dashboard"):
            lines.append(f"  dashboard: https://{GRAFANA_DOMAIN}/d/{ann['dashboard']}")
    lines.append("")
    lines.append(f"Grafana: https://{GRAFANA_DOMAIN}/d/weel-overview")
    return "\n".join(lines)


def fire(text: str) -> bool:
    if not FIRE_URL or not FIRE_TOKEN:
        _counters["relay_disabled_total"] += 1
        log("routine fire disabled (ROUTINE_FIRE_URL/TOKEN empty); text follows", text=text)
        return True
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {FIRE_TOKEN}",
        "User-Agent": "weel-alert-relay/1.0",
    }
    if BETA_HEADER and ":" in BETA_HEADER:
        k, v = BETA_HEADER.split(":", 1)
        headers[k.strip()] = v.strip()
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(FIRE_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.status
            snippet = resp.read(300).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        _counters["relay_fire_failed_total"] += 1
        log("routine fire failed", status=e.code, body=e.read(300).decode("utf-8", "replace"))
        return False
    except Exception as e:  # noqa: BLE001
        _counters["relay_fire_failed_total"] += 1
        log("routine fire error", error=str(e))
        return False
    _counters["relay_fire_total"] += 1
    log("routine fired", status=status, response=snippet)
    return True


def schedule_fire(text: str) -> str:
    """Bir vaqtda ko'p alert kelsa Routine'ni faqat bir marta uyg'otish."""
    with _lock:
        now = time.time()
        wait = _state["last_fire_at"] + MIN_INTERVAL - now
        if wait <= 0:
            _state["last_fire_at"] = now
            threading.Thread(target=fire, args=(text,), daemon=True).start()
            return "fired"
        # Throttle: keyingi ruxsat etilgan vaqtda oxirgi matn bilan bir marta uyg'otamiz.
        _counters["relay_throttled_total"] += 1
        _state["pending"] = text
        if not _state.get("timer"):
            def later():
                time.sleep(max(wait, 1))
                with _lock:
                    pending = _state.pop("pending", None)
                    _state["timer"] = None
                    _state["last_fire_at"] = time.time()
                if pending:
                    fire(pending)
            t = threading.Thread(target=later, daemon=True)
            _state["timer"] = t
            t.start()
        return f"throttled ({int(wait)}s)"


class Handler(BaseHTTPRequestHandler):
    server_version = "weel-alert-relay/1.0"

    def log_message(self, *_):  # jim
        pass

    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, "ok")
        if self.path == "/metrics":
            lines = [f"{k} {v}" for k, v in _counters.items()]
            lines.append(f"relay_configured {1 if FIRE_URL and FIRE_TOKEN else 0}")
            lines.append(f"relay_last_fire_timestamp_seconds {_state['last_fire_at']}")
            return self._send(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
        return self._send(404, "not found")

    def do_POST(self):
        if self.path != "/alertmanager":
            return self._send(404, "not found")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 1_000_000))
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._send(400, "bad json")
        _counters["relay_webhooks_received_total"] += 1
        text = build_text(payload)
        if not text:
            return self._send(200, "nothing firing")
        result = schedule_fire(text)
        log("webhook", result=result, alerts=len(payload.get("alerts", [])), group=payload.get("groupKey", ""))
        return self._send(200, result)


if __name__ == "__main__":
    log("alert-relay starting", port=PORT, fire_configured=bool(FIRE_URL and FIRE_TOKEN), min_interval=MIN_INTERVAL)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
