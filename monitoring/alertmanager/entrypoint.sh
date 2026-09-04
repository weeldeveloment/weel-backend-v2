#!/bin/sh
# Alertmanager ${ENV} ni config/template ichida kengaytirmaydi -> shablonlarni render qilamiz.
set -eu
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN env kerak}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID env kerak}"
: "${GRAFANA_DOMAIN:?GRAFANA_DOMAIN env kerak}"
render() {
  sed \
    -e "s|__TELEGRAM_BOT_TOKEN__|${TELEGRAM_BOT_TOKEN}|g" \
    -e "s|__TELEGRAM_CHAT_ID__|${TELEGRAM_CHAT_ID}|g" \
    -e "s|__GRAFANA_DOMAIN__|${GRAFANA_DOMAIN}|g" \
    "$1" > "$2"
}
mkdir -p /alertmanager/templates
render /etc/alertmanager/alertmanager.template.yml /alertmanager/alertmanager.rendered.yml
for f in /etc/alertmanager/templates/*.tmpl; do
  render "$f" "/alertmanager/templates/$(basename "$f")"
done
exec /bin/alertmanager "$@"
