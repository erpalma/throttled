#!/bin/sh
set -eu
umask 022

PACKAGE=throttled
ARCHITECTURE=all
DESCRIPTION="Workaround for Intel throttling issues in Linux."
DEPENDENCIES="python3 (>= 3.9), python3-dbus-next, pciutils, kmod, upower, systemd"
MAINTAINER="throttled maintainers <noreply@example.com>"
OUTPUT_DIR=dist
VERSION=

usage() {
    cat <<'EOF'
Usage: scripts/build-deb.sh [options]

Build a Debian package for throttled.

Options:
  --output-dir DIR      Directory where the .deb is written (default: dist)
  --version VERSION    Debian package version (default: 0.12+git.<short-sha>)
  --maintainer VALUE   Maintainer field for DEBIAN/control
  -h, --help           Show this help
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output-dir)
            [ "$#" -ge 2 ] || { echo "Missing value for --output-dir" >&2; exit 2; }
            OUTPUT_DIR=$2
            shift 2
            ;;
        --version)
            [ "$#" -ge 2 ] || { echo "Missing value for --version" >&2; exit 2; }
            VERSION=$2
            shift 2
            ;;
        --maintainer)
            [ "$#" -ge 2 ] || { echo "Missing value for --maintainer" >&2; exit 2; }
            MAINTAINER=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb is required to build the package." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "$VERSION" ]; then
    if GIT_SHA=$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null); then
        VERSION="0.12+git.$GIT_SHA"
    else
        VERSION="0.12+local"
    fi
fi

BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/throttled-deb.XXXXXX")
trap 'rm -rf "$BUILD_ROOT"' EXIT HUP INT TERM

PKG_ROOT="$BUILD_ROOT/pkg"
mkdir -p \
    "$PKG_ROOT/DEBIAN" \
    "$PKG_ROOT/etc" \
    "$PKG_ROOT/lib/systemd/system" \
    "$PKG_ROOT/usr/lib/throttled" \
    "$PKG_ROOT/usr/share/doc/throttled"

install -m 0755 "$ROOT_DIR/throttled.py" "$PKG_ROOT/usr/lib/throttled/throttled.py"
install -m 0644 "$ROOT_DIR/mmio.py" "$PKG_ROOT/usr/lib/throttled/mmio.py"
install -m 0644 "$ROOT_DIR/etc/throttled.conf" "$PKG_ROOT/etc/throttled.conf"
install -m 0644 "$ROOT_DIR/LICENSE" "$PKG_ROOT/usr/share/doc/throttled/copyright"

cat > "$PKG_ROOT/lib/systemd/system/throttled.service" <<'EOF'
[Unit]
Description=Stop Intel throttling
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/lib/throttled/throttled.py --config /etc/throttled.conf
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: $PACKAGE
Version: $VERSION
Section: admin
Priority: optional
Architecture: $ARCHITECTURE
Maintainer: $MAINTAINER
Depends: $DEPENDENCIES
Conflicts: lenovo-throttling-fix, lenovo-throttling-fix-git
Replaces: lenovo-throttling-fix, lenovo-throttling-fix-git
Description: $DESCRIPTION
 throttled applies Intel CPU package power limits, temperature targets,
 undervolt, and related settings to work around firmware throttling issues.
EOF

cat > "$PKG_ROOT/DEBIAN/conffiles" <<'EOF'
/etc/throttled.conf
EOF

cat > "$PKG_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if [ "$1" = "configure" ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
    systemctl enable throttled.service >/dev/null 2>&1 || true
    systemctl restart throttled.service || true
fi

exit 0
EOF

cat > "$PKG_ROOT/DEBIAN/prerm" <<'EOF'
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
EOF

cat > "$PKG_ROOT/DEBIAN/postrm" <<'EOF'
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
EOF

chmod 0644 "$PKG_ROOT/DEBIAN/control" "$PKG_ROOT/DEBIAN/conffiles"
chmod 0755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/prerm" "$PKG_ROOT/DEBIAN/postrm"
mkdir -p "$OUTPUT_DIR"

DEB_PATH="$OUTPUT_DIR/${PACKAGE}_${VERSION}_${ARCHITECTURE}.deb"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$DEB_PATH" >/dev/null
echo "$DEB_PATH"
