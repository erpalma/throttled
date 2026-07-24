#!/bin/sh

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
    if [ "${1:-1}" -gt 1 ] && systemctl is-active --quiet throttled.service; then
        systemctl try-restart throttled.service || true
    fi
fi

exit 0
