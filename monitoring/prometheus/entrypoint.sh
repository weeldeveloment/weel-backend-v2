#!/bin/sh
# Prometheus ${ENV} ni config ichida kengaytirmaydi. Shuning uchun shablonni
# volume ichiga render qilib, keyin asl binarni ishga tushiramiz.
set -eu
: "${BACKEND_TARGET:?BACKEND_TARGET env kerak (masalan weel-backend:8000)}"
: "${PROMETHEUS_METRICS_TOKEN:?PROMETHEUS_METRICS_TOKEN env kerak}"
sed \
  -e "s|__BACKEND_TARGET__|${BACKEND_TARGET}|g" \
  -e "s|__PROMETHEUS_METRICS_TOKEN__|${PROMETHEUS_METRICS_TOKEN}|g" \
  /etc/prometheus/prometheus.template.yml > /prometheus/prometheus.rendered.yml
exec /bin/prometheus "$@"
