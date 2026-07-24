#!/bin/sh
set -e

case "$1" in
    remove|deconfigure)
        if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
            systemctl stop throttled.service || true
            systemctl disable throttled.service >/dev/null 2>&1 || true
        fi
        ;;
esac

exit 0
