#!/bin/sh
set -eu
umask 022

PACKAGE=throttled
ARCHITECTURE=all
DESCRIPTION="Workaround for Intel throttling issues in Linux."
DEPENDENCIES="python3 (>= 3.10), python3-dbus-fast, pciutils, kmod, upower, systemd"
MAINTAINER="throttled maintainers <noreply@example.com>"
OUTPUT_DIR=dist
VERSION=

usage() {
    cat <<'EOF'
Usage: scripts/build-deb.sh [options]

Build a Debian package for throttled.

Options:
  --output-dir DIR      Directory where the .deb is written (default: dist)
  --version VERSION     Debian package version (default: <project-version>+git.<short-sha>)
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
PROJECT_VERSION=$(
    PYTHONPATH="$ROOT_DIR" python3 -c 'from throttled_version import __version__; print(__version__)'
)

if [ -z "$VERSION" ]; then
    if GIT_SHA=$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null); then
        VERSION="$PROJECT_VERSION+git.$GIT_SHA"
    else
        VERSION="$PROJECT_VERSION+local"
    fi
fi

BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/throttled-deb.XXXXXX")
trap 'rm -rf "$BUILD_ROOT"' EXIT HUP INT TERM

PKG_ROOT="$BUILD_ROOT/pkg"
mkdir -p "$PKG_ROOT/DEBIAN"
"$ROOT_DIR/scripts/stage-package.sh" "$PKG_ROOT"

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

chmod 0644 "$PKG_ROOT/DEBIAN/control" "$PKG_ROOT/DEBIAN/conffiles"
install -m 0755 "$ROOT_DIR/packaging/scripts/deb/postinstall.sh" "$PKG_ROOT/DEBIAN/postinst"
install -m 0755 "$ROOT_DIR/packaging/scripts/deb/preremove.sh" "$PKG_ROOT/DEBIAN/prerm"
install -m 0755 "$ROOT_DIR/packaging/scripts/deb/postremove.sh" "$PKG_ROOT/DEBIAN/postrm"
mkdir -p "$OUTPUT_DIR"

DEB_PATH="$OUTPUT_DIR/${PACKAGE}_${VERSION}_${ARCHITECTURE}.deb"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$DEB_PATH" >/dev/null
echo "$DEB_PATH"
