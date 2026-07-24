#!/bin/sh

if [ "${1:-1}" -eq 0 ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
    systemctl reset-failed throttled.service || true
fi

exit 0
