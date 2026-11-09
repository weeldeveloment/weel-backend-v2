#!/bin/sh
# Config'ni volume ichiga nusxalaymiz (endi sir/shablon yo'q).
set -eu
cp /etc/alertmanager/alertmanager.template.yml /alertmanager/alertmanager.rendered.yml
exec /bin/alertmanager "$@"
