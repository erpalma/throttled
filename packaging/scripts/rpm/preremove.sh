#!/bin/sh

if [ "${1:-1}" -eq 0 ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl stop throttled.service || true
    systemctl disable throttled.service >/dev/null 2>&1 || true
fi

exit 0
