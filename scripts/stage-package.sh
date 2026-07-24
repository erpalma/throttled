#!/bin/sh
set -eu
umask 022

usage() {
    echo "Usage: scripts/stage-package.sh [--with-openrc] ROOT" >&2
}

INCLUDE_OPENRC=no

if [ "${1:-}" = "--with-openrc" ]; then
    INCLUDE_OPENRC=yes
    shift
fi

if [ "$#" -ne 1 ]; then
    usage
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
STAGE_ROOT=$1

case "$STAGE_ROOT" in
    ""|"/"|"."|"$ROOT_DIR")
        echo "Refusing unsafe staging root: $STAGE_ROOT" >&2
        exit 2
        ;;
esac

mkdir -p "$STAGE_ROOT"
STAGE_ROOT=$(CDPATH= cd -- "$STAGE_ROOT" && pwd)

if [ "$STAGE_ROOT" = "$ROOT_DIR" ]; then
    echo "Refusing to stage over the source repository." >&2
    exit 2
fi

mkdir -p \
    "$STAGE_ROOT/etc" \
    "$STAGE_ROOT/usr/bin" \
    "$STAGE_ROOT/usr/lib/systemd/system" \
    "$STAGE_ROOT/usr/lib/throttled" \
    "$STAGE_ROOT/usr/share/doc/throttled"

install -m 0755 "$ROOT_DIR/throttled.py" "$STAGE_ROOT/usr/lib/throttled/throttled.py"
install -m 0644 "$ROOT_DIR/mmio.py" "$STAGE_ROOT/usr/lib/throttled/mmio.py"
install -m 0644 "$ROOT_DIR/throttled_version.py" "$STAGE_ROOT/usr/lib/throttled/throttled_version.py"
install -m 0755 "$ROOT_DIR/packaging/throttled" "$STAGE_ROOT/usr/bin/throttled"
install -m 0644 "$ROOT_DIR/etc/throttled.conf" "$STAGE_ROOT/etc/throttled.conf"
install -m 0644 "$ROOT_DIR/LICENSE" "$STAGE_ROOT/usr/share/doc/throttled/copyright"

sed \
    's|ExecStart=/opt/throttled/venv/bin/throttled|ExecStart=/usr/bin/throttled|' \
    "$ROOT_DIR/systemd/throttled.service" \
    > "$STAGE_ROOT/usr/lib/systemd/system/throttled.service"

if grep -F '/opt/throttled' "$STAGE_ROOT/usr/lib/systemd/system/throttled.service" >/dev/null; then
    echo "Failed to render the native systemd service." >&2
    exit 1
fi

chmod 0644 "$STAGE_ROOT/usr/lib/systemd/system/throttled.service"

if [ "$INCLUDE_OPENRC" = yes ]; then
    mkdir -p "$STAGE_ROOT/etc/init.d"
    sed \
        's|/opt/throttled/venv/bin/throttled|/usr/bin/throttled|' \
        "$ROOT_DIR/openrc/throttled" \
        > "$STAGE_ROOT/etc/init.d/throttled"

    if grep -F '/opt/throttled' "$STAGE_ROOT/etc/init.d/throttled" >/dev/null; then
        echo "Failed to render the native OpenRC service." >&2
        exit 1
    fi

    chmod 0755 "$STAGE_ROOT/etc/init.d/throttled"
fi
