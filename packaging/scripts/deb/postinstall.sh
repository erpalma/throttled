#!/bin/sh
set -e

if [ "$1" = "configure" ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
    systemctl enable throttled.service >/dev/null 2>&1 || true
    systemctl restart throttled.service || true
fi

exit 0
