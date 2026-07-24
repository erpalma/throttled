#!/bin/sh
set -e

case "$1" in
    remove|purge|disappear)
        if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
            systemctl daemon-reload || true
        fi
        ;;
esac

if [ "$1" = "purge" ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl reset-failed throttled.service || true
fi

exit 0
