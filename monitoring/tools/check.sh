#!/usr/bin/env bash
# Barcha monitoring configlarini rasmiy image'lar bilan tekshiradi (Docker kerak).
set -euo pipefail
cd "$(dirname "$0")/.."
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

echo "▶ prometheus config + rules"
sed -e "s|__BACKEND_TARGET__|weel-backend:8000|g" -e "s|__PROMETHEUS_METRICS_TOKEN__|x|g" prometheus/prometheus.yml > "$TMP/prometheus.yml"
docker run --rm -v "$TMP/prometheus.yml:/p/prometheus.yml:ro" -v "$PWD/prometheus/rules:/etc/prometheus/rules:ro" \
  -v "$PWD/blackbox/targets.yml:/etc/prometheus/blackbox-targets.yml:ro" \
  --entrypoint promtool prom/prometheus:v3.5.5 check config /p/prometheus.yml | grep -E "SUCCESS|FAIL"

echo "▶ alertmanager"
mkdir -p "$TMP/am/templates"
sed -e "s|__TELEGRAM_BOT_TOKEN__|1:x|g" -e "s|__TELEGRAM_CHAT_ID__|-1|g" -e "s|__GRAFANA_DOMAIN__|g|g" -e 's|/alertmanager/templates/|/am/templates/|' alertmanager/alertmanager.yml > "$TMP/am/alertmanager.yml"
sed -e "s|__GRAFANA_DOMAIN__|g|g" alertmanager/templates/telegram.tmpl > "$TMP/am/templates/telegram.tmpl"
docker run --rm -v "$TMP/am:/am:ro" --entrypoint amtool prom/alertmanager:v0.28.1 check-config /am/alertmanager.yml | grep -E "SUCCESS|FAIL"

echo "▶ loki"
docker run --rm -v "$PWD/loki/loki.yml:/etc/loki/loki.yml:ro" grafana/loki:3.6.16 -config.file=/etc/loki/loki.yml -verify-config >/dev/null && echo "  loki config ok"
docker run --rm -v "$PWD/loki/rules/fake:/r:ro" --entrypoint sh prom/prometheus:v3.5.5 -c 'promtool check rules /r/*.yml' >/dev/null 2>&1 && echo "  loki rules syntax ok (promtool)" || echo "  loki rules: promtool LogQL ni tushunmaydi — YAML tuzilishi ruler yuklanganda ko'rinadi"
python3 -c "import re,glob; [open(f).read() for f in glob.glob('loki/rules/fake/*.yml')]; print('  loki rules readable')"

echo "▶ alloy"
docker run --rm -v "$PWD/alloy/config.alloy:/c/config.alloy:ro" grafana/alloy:v1.19.2 validate /c/config.alloy && echo "  alloy config ok"

echo "▶ blackbox"
docker run --rm -v "$PWD/blackbox/blackbox.yml:/b.yml:ro" --entrypoint /bin/blackbox_exporter prom/blackbox-exporter:v0.28.0 --config.check --config.file=/b.yml 2>&1 | grep -q "Config file is ok" && echo "  blackbox ok"

echo "▶ compose"
docker compose -f docker-compose.yml --env-file .env.example config >/dev/null && echo "  compose ok"

echo "▶ dashboards"
python3 -c "
import json,glob
n=0
for f in sorted(glob.glob('grafana/dashboards/*.json')):
    d=json.load(open(f)); n+=1
    assert d['uid'] and d['title'], f
print(f'  {n} dashboards ok')"
echo "✔ hammasi joyida"
