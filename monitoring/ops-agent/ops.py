"""
weel ops-agent: bulutdagi Claude Routine uchun BITTA HTTP API —
kuzatish (Prometheus/Loki/Tempo/Alertmanager/konteynerlar) + ruxsat etilgan
tuzatish amallari (restart, exec whitelist, disk prune, Dokploy redeploy) +
Telegram xabar. Faqat stdlib — image: python:3.12-alpine, build kerak emas.

Xavfsizlik:
  * Har so'rov `Authorization: Bearer $OPS_TOKEN` talab qiladi (const-time solishtiruv).
  * Tashqariga Traefik orqali faqat  https://$MCP_DOMAIN/ops/...  chiqadi.
  * Amallar cheklangan: restart faqat OPS_ALLOW_REGEX ga mos va OPS_DENY_REGEX ga
    mos bo'lmagan konteynerlarda; exec faqat oq ro'yxatdagi buyruqlar; SQL faqat
    SELECT / pg_terminate_backend / pg_cancel_backend.
  * Soatlik limit (OPS_MAX_ACTIONS_PER_HOUR) — halqaga tushib ketmaslik uchun.
  * Har amal JSON qatorda stdout'ga yoziladi (Alloy -> Loki, {container=~".*ops-agent.*"})
    va /ops/actions da oxirgi 200 tasi ko'rinadi; /ops/metrics Prometheus uchun.

Env:
  OPS_TOKEN                   majburiy; bo'sh bo'lsa xizmat 503 qaytaradi
  OPS_ALLOW_REGEX             restart/exec/logs uchun konteyner nomi regex (default: hammasi)
  OPS_DENY_REGEX              hech qachon tegilmaydigan konteynerlar (default: dokploy|traefik)
  OPS_MAX_ACTIONS_PER_HOUR    default 12
  DOKPLOY_URL, DOKPLOY_API_KEY  ixtiyoriy — /ops/redeploy uchun
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   /ops/notify uchun
  PROMETHEUS_URL, LOKI_URL, TEMPO_URL, ALERTMANAGER_URL   ichki manzillar
  GRAFANA_DOMAIN              matndagi havolalar uchun
"""
from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import shlex
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.getenv("OPS_TOKEN", "").strip()
ALLOW_RE = re.compile(os.getenv("OPS_ALLOW_REGEX", ".*"))
DENY_RE = re.compile(os.getenv("OPS_DENY_REGEX", r".*(dokploy|traefik).*"))
MAX_PER_HOUR = int(os.getenv("OPS_MAX_ACTIONS_PER_HOUR", "12") or "12")
DOKPLOY_URL = os.getenv("DOKPLOY_URL", "").rstrip("/")
DOKPLOY_KEY = os.getenv("DOKPLOY_API_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PROM = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
LOKI = os.getenv("LOKI_URL", "http://loki:3100").rstrip("/")
TEMPO = os.getenv("TEMPO_URL", "http://tempo:3200").rstrip("/")
AM = os.getenv("ALERTMANAGER_URL", "http://alertmanager:9093").rstrip("/")
GRAFANA_DOMAIN = os.getenv("GRAFANA_DOMAIN", "grafana.weel.uz")
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
PORT = int(os.getenv("OPS_PORT", "8090"))

_lock = threading.Lock()
_actions: deque = deque(maxlen=200)      # audit
_action_times: deque = deque()           # rate limit
_counters = {
    "ops_requests_total": 0,
    "ops_unauthorized_total": 0,
    "ops_actions_total": 0,
    "ops_actions_failed_total": 0,
    "ops_actions_rate_limited_total": 0,
    "ops_notify_total": 0,
}

# exec oq ro'yxati: (konteyner regex, buyruq prefiksi). Prefiks shlex bo'yicha tokenlarda.
EXEC_ALLOW: list[tuple[str, list[str]]] = [
    (r".*backend.*", ["python", "manage.py", "check"]),
    (r".*backend.*", ["python", "manage.py", "showmigrations"]),
    (r".*backend.*", ["python", "manage.py", "migrate"]),
    (r".*backend.*", ["python", "manage.py", "create_b2b_tables"]),
    (r".*backend.*", ["python", "manage.py", "create_hotels_tables"]),
    (r".*backend.*", ["python", "manage.py", "create_avia_tables"]),
    (r".*backend.*", ["python", "manage.py", "collectstatic", "--noinput"]),
    (r".*backend.*", ["celery", "-A", "core", "inspect"]),
    (r".*backend.*", ["celery", "-A", "core", "purge", "-f"]),
    (r".*backend.*", ["df", "-h"]),
    (r".*backend.*", ["du", "-sh"]),
    (r".*backend.*", ["find"]),
    (r".*backend.*", ["ls"]),
    (r".*backend.*", ["cat"]),
    # `env` ATAYIN yo'q — sirlarni chiqarib yuboradi; kalit nomlari GET /container da (env_keys).
    (r".*redis.*", ["redis-cli"]),
    (r".*postgres.*", ["psql"]),
    (r".*postgres.*", ["pg_isready"]),
    (r".*", ["cat", "/proc/meminfo"]),
]
SQL_ALLOW_RE = re.compile(
    r"^\s*(select\b|with\b|show\b|explain\b|vacuum\s*\(?\s*analyze|analyze\b)", re.IGNORECASE
)
SQL_DENY_RE = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy)\b", re.IGNORECASE)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg: str, **kv) -> None:
    print(json.dumps({"ts": now_iso(), "msg": msg, **kv}, ensure_ascii=False, default=str), flush=True)


class Denied(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# ───────────────────────────── Docker (unix socket) ─────────────────────────
class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 60):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def docker(method: str, path: str, body: dict | None = None, raw: bool = False, timeout: float = 60):
    conn = _UnixConn(DOCKER_SOCK, timeout=timeout)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    if resp.status >= 400:
        try:
            msg = json.loads(payload).get("message", payload.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            msg = payload.decode("utf-8", "replace")
        raise Denied(502, f"docker {resp.status}: {msg}")
    if raw:
        return payload
    return json.loads(payload) if payload else None


def demux(stream: bytes) -> str:
    """Docker multiplexed log stream (8 byte header) -> matn."""
    out, i = [], 0
    while i + 8 <= len(stream):
        size = int.from_bytes(stream[i + 4:i + 8], "big")
        out.append(stream[i + 8:i + 8 + size])
        i += 8 + size
    if not out:  # tty konteyner — header yo'q
        return stream.decode("utf-8", "replace")
    return b"".join(out).decode("utf-8", "replace")


def list_containers() -> list[dict]:
    items = docker("GET", "/containers/json?all=1")
    res = []
    for c in items:
        name = (c.get("Names") or ["/?"])[0].lstrip("/")
        res.append({
            "name": name,
            "image": c.get("Image"),
            "state": c.get("State"),
            "status": c.get("Status"),
            "created": c.get("Created"),
            "labels": {k: v for k, v in (c.get("Labels") or {}).items()
                       if k.startswith("com.docker.compose.") or k.startswith("com.docker.swarm.service.name")},
        })
    return res


def resolve_container(name: str) -> str:
    """Nom yoki regex -> bitta aniq konteyner nomi (running/any)."""
    if not name:
        raise Denied(400, "name kerak")
    names = [c["name"] for c in list_containers()]
    if name in names:
        return name
    try:
        rx = re.compile(name)
    except re.error:
        raise Denied(400, "noto'g'ri regex")
    hits = [n for n in names if rx.search(n)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise Denied(404, f"konteyner topilmadi: {name}")
    raise Denied(409, f"bir nechta mos keldi: {hits[:10]} — aniqroq nom bering")


def guard_container(name: str) -> None:
    if DENY_RE.search(name):
        raise Denied(403, f"{name} — OPS_DENY_REGEX bo'yicha tegib bo'lmaydi")
    if not ALLOW_RE.search(name):
        raise Denied(403, f"{name} — OPS_ALLOW_REGEX ga mos emas")


def container_inspect(name: str) -> dict:
    d = docker("GET", f"/containers/{urllib.parse.quote(name)}/json")
    st = d.get("State", {})
    hc = (st.get("Health") or {})
    return {
        "name": name,
        "image": d.get("Config", {}).get("Image"),
        "state": st.get("Status"),
        "running": st.get("Running"),
        "restarting": st.get("Restarting"),
        "oom_killed": st.get("OOMKilled"),
        "exit_code": st.get("ExitCode"),
        "started_at": st.get("StartedAt"),
        "finished_at": st.get("FinishedAt"),
        "restart_count": d.get("RestartCount"),
        "health": hc.get("Status"),
        "health_log": [(h.get("End"), h.get("ExitCode"), (h.get("Output") or "")[:200]) for h in (hc.get("Log") or [])[-3:]],
        "memory_limit": d.get("HostConfig", {}).get("Memory"),
        "restart_policy": d.get("HostConfig", {}).get("RestartPolicy", {}).get("Name"),
        "env_keys": sorted(e.split("=", 1)[0] for e in d.get("Config", {}).get("Env") or []),
    }


def container_logs(name: str, tail: int = 200, since_s: int = 0, grep: str | None = None) -> str:
    q = f"stdout=1&stderr=1&tail={max(1, min(tail, 2000))}&timestamps=1"
    if since_s:
        q += f"&since={int(time.time()) - since_s}"
    raw = docker("GET", f"/containers/{urllib.parse.quote(name)}/logs?{q}", raw=True)
    text = demux(raw)
    if grep:
        rx = re.compile(grep, re.IGNORECASE)
        text = "\n".join(l for l in text.splitlines() if rx.search(l))
    return text[-200_000:]


def container_stats(name: str) -> dict:
    s = docker("GET", f"/containers/{urllib.parse.quote(name)}/stats?stream=false", timeout=20)
    mem = s.get("memory_stats", {})
    cpu = s.get("cpu_stats", {}); pre = s.get("precpu_stats", {})
    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    ncpu = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage") or [1])
    pct = (cpu_delta / sys_delta * ncpu * 100) if sys_delta > 0 else 0
    return {"name": name, "cpu_percent": round(pct, 1), "mem_usage": mem.get("usage"), "mem_limit": mem.get("limit"),
            "pids": s.get("pids_stats", {}).get("current")}


def container_restart(name: str, timeout: int = 20) -> dict:
    docker("POST", f"/containers/{urllib.parse.quote(name)}/restart?t={int(timeout)}", timeout=timeout + 30)
    time.sleep(2)
    return container_inspect(name)


_ENV_REF = re.compile(r"\$ENV:([A-Z0-9_]+)")


def resolve_env_refs(name: str, cmd: list[str]) -> list[str]:
    """`$ENV:NAME` tokenlarini konteynerning o'z env qiymatiga almashtiradi (server tomonda —
    qiymat so'rovchiga qaytmaydi). Misol: redis-cli -a $ENV:REDIS_PASSWORD info memory;
    psql -U $ENV:POSTGRES_USER -d $ENV:POSTGRES_DB -c "SELECT 1"."""
    if not any(_ENV_REF.search(t) for t in cmd):
        return cmd
    d = docker("GET", f"/containers/{urllib.parse.quote(name)}/json")
    env = dict(e.split("=", 1) for e in (d.get("Config", {}).get("Env") or []) if "=" in e)
    out = []
    for t in cmd:
        def sub(m):
            if m.group(1) not in env:
                raise Denied(400, f"konteynerda {m.group(1)} env yo'q")
            return env[m.group(1)]
        out.append(_ENV_REF.sub(sub, t))
    return out


def redact(text: str, cmd: list[str], real: list[str]) -> str:
    """Almashtirilgan sir qiymatlari chiqishda ko'rinib qolsa, yashiramiz."""
    for a, b in zip(cmd, real):
        if a != b:
            for secret in (b, *[b[len(p):] for p in ("-a", "-p") if b.startswith(p) and len(b) > 4]):
                if len(secret) >= 4:
                    text = text.replace(secret, "***")
    return text


def service_inspect(name: str) -> dict:
    """Docker Swarm service (Dokploy ilovalari shunday deploy bo'ladi): image, replicas, labels, portlar."""
    svcs = docker("GET", f"/services?filters={urllib.parse.quote(json.dumps({'name': [name]}))}")
    hits = [s for s in svcs if s.get("Spec", {}).get("Name") == name] or svcs
    if not hits:
        try:
            rx = re.compile(name)
        except re.error:
            raise Denied(400, "noto'g'ri regex")
        hits = [s for s in docker("GET", "/services") if rx.search(s.get("Spec", {}).get("Name", ""))]
    if not hits:
        raise Denied(404, f"service topilmadi: {name}")
    if len(hits) > 1:
        raise Denied(409, f"bir nechta service: {[s['Spec']['Name'] for s in hits][:10]}")
    s = hits[0]
    spec = s.get("Spec", {}); task = spec.get("TaskTemplate", {}); cs = task.get("ContainerSpec", {})
    labels = {k: v for k, v in (spec.get("Labels") or {}).items()}
    return {
        "name": spec.get("Name"), "id": s.get("ID"), "image": cs.get("Image"),
        "replicas": (spec.get("Mode", {}).get("Replicated") or {}).get("Replicas"),
        "update_status": s.get("UpdateStatus"),
        "traefik_labels": {k: v for k, v in labels.items() if k.startswith("traefik.")},
        "other_labels": {k: v for k, v in labels.items() if not k.startswith("traefik.")},
        "ports": (spec.get("EndpointSpec") or {}).get("Ports"),
        "networks": [n.get("Target") for n in task.get("Networks") or []],
        "env_keys": sorted(e.split("=", 1)[0] for e in cs.get("Env") or []),
        "healthcheck": cs.get("Healthcheck"),
        "resources": task.get("Resources"),
        "restart_policy": task.get("RestartPolicy"),
        "created_at": s.get("CreatedAt"), "updated_at": s.get("UpdatedAt"),
    }


def container_exec(name: str, cmd: list[str], timeout: int = 120) -> dict:
    real = resolve_env_refs(name, cmd)
    ex = docker("POST", f"/containers/{urllib.parse.quote(name)}/exec",
                {"AttachStdout": True, "AttachStderr": True, "Cmd": real})
    exec_id = ex["Id"]
    out = docker("POST", f"/exec/{exec_id}/start", {"Detach": False, "Tty": False}, raw=True, timeout=timeout)
    info = docker("GET", f"/exec/{exec_id}/json")
    return {"exit_code": info.get("ExitCode"), "output": redact(demux(out)[-100_000:], cmd, real)}


def check_exec_allowed(name: str, cmd: list[str]) -> None:
    if not cmd:
        raise Denied(400, "cmd bo'sh")
    for rx, prefix in EXEC_ALLOW:
        if re.search(rx, name) and cmd[: len(prefix)] == prefix:
            break
    else:
        raise Denied(403, f"exec ruxsat etilmagan: {' '.join(cmd)[:120]} (oq ro'yxat: monitoring/ops-agent/ops.py EXEC_ALLOW)")
    joined = " ".join(cmd)
    if re.search(r"[;&|`$><]", joined):
        raise Denied(403, "shell metabelgilar ruxsat etilmagan")
    if cmd[0] == "psql":
        # faqat -c "<sql>" shakli; sql SELECT/terminate bo'lishi shart
        sqls = [cmd[i + 1] for i, t in enumerate(cmd[:-1]) if t in ("-c", "--command")]
        if not sqls or any(t in ("-f", "--file") for t in cmd):
            raise Denied(403, "psql faqat -c \"<sql>\" bilan")
        for s in sqls:
            if not SQL_ALLOW_RE.search(s) or SQL_DENY_RE.search(s):
                raise Denied(403, f"SQL ruxsat etilmagan: {s[:120]}")
    if cmd[0] == "redis-cli":
        allowed = {"info", "ping", "dbsize", "config", "client", "memory", "slowlog", "llen", "type", "ttl", "scan", "keys", "get", "hgetall", "lrange", "del"}
        sub = [t for t in cmd[1:] if not t.startswith("-")]
        # -a <parol> qiymatini o'tkazib yuboramiz
        skip = False; real = []
        for t in cmd[1:]:
            if skip: skip = False; continue
            if t in ("-a", "-h", "-p", "-n", "-u"): skip = True; continue
            real.append(t)
        if not real or real[0].lower() not in allowed:
            raise Denied(403, f"redis-cli buyrug'i ruxsat etilmagan: {real[:1]}")
        if real[0].lower() == "config" and len(real) > 1 and real[1].lower() != "get":
            raise Denied(403, "redis CONFIG faqat GET")
        if real[0].lower() == "del" and not all(k.startswith("celery") or k.startswith("_kombu") or k.startswith("unacked") for k in real[1:]):
            raise Denied(403, "redis DEL faqat celery/_kombu/unacked kalitlarda")
    if cmd[:3] == ["python", "manage.py", "migrate"] and any(t in ("--fake", "--fake-initial", "zero") for t in cmd):
        raise Denied(403, "migrate --fake / zero ruxsat etilmagan")


def docker_df() -> dict:
    d = docker("GET", "/system/df", timeout=120)
    imgs = d.get("Images") or []
    cons = d.get("Containers") or []
    vols = d.get("Volumes") or []
    return {
        "images": {"count": len(imgs), "size": sum(i.get("Size", 0) for i in imgs),
                   "reclaimable": sum(i.get("Size", 0) for i in imgs if (i.get("Containers") or 0) <= 0)},
        "containers": {"count": len(cons), "rw_size": sum(c.get("SizeRw", 0) or 0 for c in cons),
                       "stopped": [(c.get("Names") or ["?"])[0].lstrip("/") for c in cons if c.get("State") != "running"]},
        "volumes": sorted(({"name": v.get("Name"), "size": (v.get("UsageData") or {}).get("Size"),
                            "refs": (v.get("UsageData") or {}).get("RefCount")} for v in vols),
                          key=lambda x: -(x["size"] or 0))[:25],
        "build_cache": sum((b.get("Size") or 0) for b in (d.get("BuildCache") or []) if not b.get("InUse")),
    }


def docker_prune(what: str) -> dict:
    res = {}
    if what in ("images", "safe", "all"):
        f = urllib.parse.quote(json.dumps({"dangling": ["false"], "until": ["24h"]}))
        r = docker("POST", f"/images/prune?filters={f}", timeout=300) or {}
        res["images"] = {"deleted": len(r.get("ImagesDeleted") or []), "reclaimed": r.get("SpaceReclaimed", 0)}
    if what in ("build", "safe", "all"):
        r = docker("POST", "/build/prune?all=1", timeout=300) or {}
        res["build_cache"] = {"reclaimed": r.get("SpaceReclaimed", 0)}
    if what in ("containers", "all"):
        f = urllib.parse.quote(json.dumps({"until": ["24h"]}))
        r = docker("POST", f"/containers/prune?filters={f}", timeout=300) or {}
        res["containers"] = {"deleted": len(r.get("ContainersDeleted") or []), "reclaimed": r.get("SpaceReclaimed", 0)}
    if not res:
        raise Denied(400, "what: images|build|containers|safe|all")
    return res


# ───────────────────────────── Dokploy ─────────────────────────
def dokploy(path: str, body: dict | None = None, method: str = "POST") -> dict:
    if not DOKPLOY_URL or not DOKPLOY_KEY:
        raise Denied(503, "DOKPLOY_URL/DOKPLOY_API_KEY sozlanmagan")
    url = f"{DOKPLOY_URL}/api/{path}"
    data = json.dumps(body or {}).encode() if method == "POST" else None
    if method == "GET" and body:
        url += "?" + urllib.parse.urlencode({"input": json.dumps({"json": body})})
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"x-api-key": DOKPLOY_KEY, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise Denied(502, f"dokploy {e.code}: {e.read(300).decode('utf-8', 'replace')}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"raw": raw[:500].decode("utf-8", "replace")}


def dokploy_apps() -> list[dict]:
    projects = dokploy("project.all", method="GET")
    if isinstance(projects, dict) and "result" in projects:
        projects = projects["result"].get("data", {}).get("json", projects["result"])
    out = []
    for p in projects or []:
        envs = p.get("environments") or [p]
        for env in envs:
            for a in env.get("applications") or []:
                out.append({"kind": "application", "id": a.get("applicationId"), "name": a.get("name"), "appName": a.get("appName"),
                            "status": a.get("applicationStatus"), "project": p.get("name")})
            for c in env.get("compose") or []:
                out.append({"kind": "compose", "id": c.get("composeId"), "name": c.get("name"), "appName": c.get("appName"),
                            "status": c.get("composeStatus"), "project": p.get("name")})
    return out


# ───────────────────────────── Observability proxy ─────────────────────────
def http_json(url: str, timeout: int = 30, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Denied(502, f"{url.split('/api')[0]} {e.code}: {e.read(300).decode('utf-8', 'replace')}")
    except Exception as e:  # noqa: BLE001
        raise Denied(502, f"{url.split('/api')[0]}: {e}")


def prom_query(q: str, start: str | None, end: str | None, step: str | None) -> dict:
    if start or end:
        params = {"query": q, "start": start or str(time.time() - 3600), "end": end or str(time.time()), "step": step or "60s"}
        return http_json(f"{PROM}/api/v1/query_range?{urllib.parse.urlencode(params)}")
    return http_json(f"{PROM}/api/v1/query?{urllib.parse.urlencode({'query': q})}")


def loki_query(q: str, start: str | None, end: str | None, limit: int) -> dict:
    now_ns = int(time.time() * 1e9)
    params = {"query": q, "limit": str(max(1, min(limit, 500))), "direction": "backward",
              "start": start or str(now_ns - 3600 * 10**9), "end": end or str(now_ns)}
    return http_json(f"{LOKI}/loki/api/v1/query_range?{urllib.parse.urlencode(params)}")


def tempo_search(q: str, limit: int) -> dict:
    params = {"q": q, "limit": str(max(1, min(limit, 50))), "start": str(int(time.time()) - 3600), "end": str(int(time.time()))}
    return http_json(f"{TEMPO}/api/search?{urllib.parse.urlencode(params)}")


def alerts_active() -> list[dict]:
    items = http_json(f"{AM}/api/v2/alerts?active=true&silenced=false&inhibited=false")
    out = []
    for a in items if isinstance(items, list) else []:
        l = a.get("labels", {}); an = a.get("annotations", {})
        out.append({"alertname": l.get("alertname"), "severity": l.get("severity"), "service": l.get("service"),
                    "instance": l.get("instance") or l.get("name") or l.get("app"), "startsAt": a.get("startsAt"),
                    "summary": an.get("summary"), "description": an.get("description"), "dashboard": an.get("dashboard")})
    return out


def telegram(text: str, silent: bool = False) -> dict:
    if not TG_TOKEN or not TG_CHAT:
        raise Denied(503, "TELEGRAM_BOT_TOKEN/CHAT_ID sozlanmagan")
    body = json.dumps({"chat_id": TG_CHAT, "text": text[:4000], "disable_web_page_preview": True,
                       "disable_notification": silent}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _counters["ops_notify_total"] += 1
            return {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        raise Denied(502, f"telegram {e.code}: {e.read(300).decode('utf-8', 'replace')}")


# ───────────────────────────── Amallar (audit + limit) ─────────────────────────
def record_action(kind: str, target: str, ok: bool, detail: str, actor: str) -> None:
    with _lock:
        _actions.appendleft({"ts": now_iso(), "action": kind, "target": target, "ok": ok, "detail": detail[:300], "actor": actor})
    _counters["ops_actions_total"] += 1
    if not ok:
        _counters["ops_actions_failed_total"] += 1
    log("action", action=kind, target=target, ok=ok, detail=detail[:500], actor=actor)


def rate_limit() -> None:
    with _lock:
        now = time.time()
        while _action_times and _action_times[0] < now - 3600:
            _action_times.popleft()
        if len(_action_times) >= MAX_PER_HOUR:
            _counters["ops_actions_rate_limited_total"] += 1
            raise Denied(429, f"soatlik amal limiti ({MAX_PER_HOUR}) tugadi — odam aralashsin")
        _action_times.append(now)


class Handler(BaseHTTPRequestHandler):
    server_version = "weel-ops-agent/1.0"

    def log_message(self, *_):
        pass

    # ── yordamchi ──
    def _json(self, code: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _text(self, code: int, body: str, ctype="text/plain; charset=utf-8") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self) -> str:
        if not TOKEN:
            raise Denied(503, "OPS_TOKEN sozlanmagan")
        h = self.headers.get("Authorization", "")
        tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
        if not tok or not hmac.compare_digest(tok, TOKEN):
            _counters["ops_unauthorized_total"] += 1
            raise Denied(401, "unauthorized")
        return (self.headers.get("X-Actor") or "claude")[:60]

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 200_000)) if n else b""
        if not raw:
            return {}
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            raise Denied(400, "bad json")
        return d if isinstance(d, dict) else {}

    def _route(self, method: str):
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip("/")
        if path.startswith("/ops"):
            path = path[4:] or "/"
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        _counters["ops_requests_total"] += 1

        # auth'siz
        if path in ("/", "/health", "/healthz") and method == "GET":
            return self._json(200, {"ok": True, "service": "weel-ops-agent", "token_configured": bool(TOKEN),
                                    "dokploy": bool(DOKPLOY_URL and DOKPLOY_KEY), "telegram": bool(TG_TOKEN and TG_CHAT)})
        if path == "/metrics" and method == "GET":
            lines = [f"{k} {v}" for k, v in _counters.items()]
            lines.append(f"ops_token_configured {1 if TOKEN else 0}")
            lines.append(f"ops_actions_last_hour {len(_action_times)}")
            return self._text(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")

        actor = self._auth()
        body = self._body() if method == "POST" else {}

        # ── kuzatish ──
        if method == "GET" and path == "/help":
            return self._json(200, HELP)
        if method == "GET" and path == "/status":
            return self._json(200, overview())
        if method == "GET" and path == "/alerts":
            return self._json(200, alerts_active())
        if method == "GET" and path == "/containers":
            return self._json(200, list_containers())
        if method == "GET" and path == "/container":
            name = resolve_container(q.get("name", ""))
            res = container_inspect(name)
            try:
                res["stats"] = container_stats(name) if res.get("running") else None
            except Exception as e:  # noqa: BLE001
                res["stats_error"] = str(e)
            return self._json(200, res)
        if method == "GET" and path == "/logs":
            # O'qish — deny ro'yxatiga qaramay (traefik/dokploy loglari diagnostika uchun kerak).
            name = resolve_container(q.get("name", ""))
            return self._text(200, container_logs(name, int(q.get("tail", "200")), int(q.get("since", "0")), q.get("grep")))
        if method == "GET" and path == "/service":
            return self._json(200, service_inspect(q.get("name", "")))
        if method == "GET" and path == "/disk":
            return self._json(200, docker_df())
        if method == "GET" and path == "/actions":
            return self._json(200, list(_actions))
        if method in ("GET", "POST") and path == "/query":
            p = {**q, **body}
            ds = p.get("ds", "prometheus")
            if ds == "prometheus":
                return self._json(200, prom_query(p.get("q") or p.get("query", ""), p.get("start"), p.get("end"), p.get("step")))
            if ds == "loki":
                return self._json(200, loki_query(p.get("q") or p.get("query", ""), p.get("start"), p.get("end"), int(p.get("limit", 100))))
            if ds == "tempo":
                return self._json(200, tempo_search(p.get("q") or p.get("query", ""), int(p.get("limit", 20))))
            raise Denied(400, "ds: prometheus|loki|tempo")
        if method == "GET" and path == "/dokploy/apps":
            return self._json(200, dokploy_apps())

        # ── amallar ──
        if method == "POST" and path == "/notify":
            text = (body.get("text") or "").strip()
            if not text:
                raise Denied(400, "text kerak")
            return self._json(200, telegram(text, bool(body.get("silent"))))

        if method == "POST" and path == "/restart":
            name = resolve_container(body.get("name", ""))
            guard_container(name)
            rate_limit()
            try:
                res = container_restart(name, int(body.get("timeout", 20)))
            except Exception as e:  # noqa: BLE001
                record_action("restart", name, False, str(e), actor)
                raise
            record_action("restart", name, True, f"state={res.get('state')} health={res.get('health')} reason={body.get('reason', '')}", actor)
            return self._json(200, res)

        if method == "POST" and path == "/exec":
            name = resolve_container(body.get("name", ""))
            guard_container(name)
            cmd = body.get("cmd")
            if isinstance(cmd, str):
                cmd = shlex.split(cmd)
            if not isinstance(cmd, list) or not all(isinstance(t, str) for t in cmd):
                raise Denied(400, "cmd: list yoki string")
            check_exec_allowed(name, cmd)
            mutating = cmd[:3] == ["python", "manage.py", "migrate"] or "purge" in cmd or "terminate" in " ".join(cmd).lower() \
                or "cancel_backend" in " ".join(cmd).lower() or (cmd[0] == "redis-cli" and "del" in [t.lower() for t in cmd])
            if mutating:
                rate_limit()
            try:
                res = container_exec(name, cmd, int(body.get("timeout", 120)))
            except Exception as e:  # noqa: BLE001
                if mutating:
                    record_action("exec", name, False, f"{' '.join(cmd)[:150]}: {e}", actor)
                raise
            if mutating:
                record_action("exec", name, res.get("exit_code") == 0, f"{' '.join(cmd)[:150]} exit={res.get('exit_code')}", actor)
            else:
                log("exec-read", target=name, cmd=" ".join(cmd)[:150], exit=res.get("exit_code"), actor=actor)
            return self._json(200, res)

        if method == "POST" and path == "/prune":
            what = body.get("what", "safe")
            if what not in ("images", "build", "containers", "safe", "all"):
                raise Denied(400, "what: images|build|containers|safe|all")
            rate_limit()
            try:
                res = docker_prune(what)
            except Exception as e:  # noqa: BLE001
                record_action("prune", what, False, str(e), actor)
                raise
            record_action("prune", what, True, json.dumps(res), actor)
            return self._json(200, res)

        if method == "POST" and path == "/redeploy":
            kind = body.get("kind", "application"); _id = body.get("id", "")
            if not _id:
                raise Denied(400, "id kerak (GET /ops/dokploy/apps)")
            rate_limit()
            try:
                res = dokploy(f"{kind}.redeploy", {f"{kind}Id": _id})
            except Exception as e:  # noqa: BLE001
                record_action("redeploy", f"{kind}:{_id}", False, str(e), actor)
                raise
            record_action("redeploy", f"{kind}:{_id}", True, body.get("reason", ""), actor)
            return self._json(200, {"ok": True, "result": res})

        if method == "POST" and path == "/silence":
            # Alertmanager'da vaqtinchalik jimlik (default 2 soat) — takroriy uyg'otishlarni to'xtatish uchun
            alertname = body.get("alertname", "")
            if not alertname:
                raise Denied(400, "alertname kerak")
            hours = min(float(body.get("hours", 2)), 24)
            payload = {"matchers": [{"name": "alertname", "value": alertname, "isRegex": False, "isEqual": True}],
                       "startsAt": now_iso(), "endsAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + hours * 3600)),
                       "createdBy": actor, "comment": body.get("comment", "ops-agent silence")}
            req = urllib.request.Request(f"{AM}/api/v2/silences", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    res = json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                raise Denied(502, f"alertmanager {e.code}: {e.read(300).decode('utf-8', 'replace')}")
            record_action("silence", alertname, True, f"{hours}h {body.get('comment', '')}", actor)
            return self._json(200, res)

        raise Denied(404, "not found — GET /ops/help")

    def _handle(self, method: str):
        try:
            self._route(method)
        except Denied as e:
            self._json(e.code, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log("handler error", error=repr(e), path=self.path)
            self._json(500, {"error": repr(e)})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def overview() -> dict:
    """Bitta so'rovda: alertlar + golden signals + konteynerlar + disk. Routine avval shuni oladi."""
    out: dict = {"ts": now_iso(), "grafana": f"https://{GRAFANA_DOMAIN}/d/weel-overview"}
    try:
        out["alerts"] = alerts_active()
    except Exception as e:  # noqa: BLE001
        out["alerts_error"] = str(e)
    sig = {
        "backend_up": 'max(up{job="weel-backend"})',
        "rps": "sum(rate(django_http_requests_total_by_view_transport_method_total[5m]))",
        "err5xx_ratio": 'sum(rate(django_http_responses_total_by_status_total{status=~"5.."}[5m])) / clamp_min(sum(rate(django_http_responses_total_by_status_total[5m])),0.001)',
        "p95_s": "histogram_quantile(0.95, sum(rate(django_http_requests_latency_seconds_by_view_method_bucket[5m])) by (le))",
        "celery_workers": "sum(celery_worker_up)",
        "celery_failed_15m": "sum(rate(celery_task_failed_total[15m]))*900",
        "pg_up": "max(pg_up)",
        "redis_up": "max(redis_up)",
        "cpu_pct": '100*(1-avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))',
        "mem_pct": "100*(1-node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)",
        "disk_root_pct": '100*(1-node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}/node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"})',
        "probes_down": "count(probe_success==0) or vector(0)",
        "errors_1h_logs": None,
    }
    signals = {}
    for k, q in sig.items():
        if not q:
            continue
        try:
            r = prom_query(q, None, None, None).get("data", {}).get("result", [])
            signals[k] = round(float(r[0]["value"][1]), 3) if r else None
        except Exception as e:  # noqa: BLE001
            signals[k] = f"err: {e}"
    try:
        r = loki_query('sum(count_over_time({service="weel-backend", level=~"error|critical"}[1h]))', None, None, 1)
        res = r.get("data", {}).get("result", [])
        signals["errors_1h_logs"] = float(res[0]["value"][1]) if res and "value" in res[0] else 0
    except Exception as e:  # noqa: BLE001
        signals["errors_1h_logs"] = f"err: {e}"
    out["signals"] = signals
    try:
        cs = list_containers()
        out["containers"] = [{"name": c["name"], "state": c["state"], "status": c["status"]} for c in cs
                             if c["state"] != "running" or re.search(r"weel|backend|postgres|redis|b2b", c["name"], re.I)]
    except Exception as e:  # noqa: BLE001
        out["containers_error"] = str(e)
    try:
        out["recent_actions"] = list(_actions)[:10]
    except Exception:  # noqa: BLE001
        pass
    return out


HELP = {
    "auth": "Authorization: Bearer $OPS_TOKEN  (ixtiyoriy X-Actor: <kim>)",
    "read": {
        "GET /ops/status": "alertlar + golden signals + muhim konteynerlar + oxirgi amallar (avval shuni ol)",
        "GET /ops/alerts": "Alertmanager'dagi faol alertlar",
        "GET /ops/containers": "barcha konteynerlar",
        "GET /ops/container?name=<nom|regex>": "inspect + stats (restartlar, OOM, health, xotira)",
        "GET /ops/logs?name=<nom|regex>&tail=200&since=<sekund>&grep=<regex>": "konteyner loglari (matn; traefik/dokploy ham o'qiladi)",
        "GET /ops/service?name=<swarm service nomi|regex>": "Dokploy ilovasi (swarm service): image, replicas, traefik label'lari, portlar, update status",
        "GET /ops/disk": "docker system df (image/volume/build cache)",
        "GET|POST /ops/query  {ds: prometheus|loki|tempo, q, start?, end?, step?, limit?}": "PromQL / LogQL / TraceQL",
        "GET /ops/dokploy/apps": "Dokploy ilovalari (id, nom, status) — redeploy uchun",
        "GET /ops/actions": "oxirgi 200 amal (audit)",
    },
    "act": {
        "POST /ops/restart {name, reason}": "konteynerni restart (deny: dokploy/traefik)",
        "POST /ops/exec {name, cmd, reason}": "oq ro'yxatdagi buyruq: manage.py migrate/check/showmigrations/create_*_tables, celery inspect, psql -c 'SELECT…|pg_terminate_backend', redis-cli info/…; $ENV:NAME -> konteyner env qiymati (server tomonda, masalan -a $ENV:REDIS_PASSWORD)",
        "POST /ops/prune {what: safe|images|build|containers|all}": "disk tozalash (24h dan eski, ishlatilmayotgan)",
        "POST /ops/redeploy {kind: application|compose, id, reason}": "Dokploy redeploy (oxirgi image bilan qayta ko'tarish)",
        "POST /ops/silence {alertname, hours, comment}": "Alertmanager'da alertni vaqtincha jimlash",
        "POST /ops/notify {text, silent?}": "Telegram guruhga xabar",
    },
    "limits": {"actions_per_hour": MAX_PER_HOUR, "deny_regex": DENY_RE.pattern, "allow_regex": ALLOW_RE.pattern},
}


if __name__ == "__main__":
    log("ops-agent starting", port=PORT, token_configured=bool(TOKEN), dokploy=bool(DOKPLOY_URL and DOKPLOY_KEY),
        telegram=bool(TG_TOKEN and TG_CHAT), max_actions_per_hour=MAX_PER_HOUR)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
