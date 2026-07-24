#!/bin/sh
set -eu
umask 022

OUTPUT_DIR=dist
PACKAGE_VERSION=
PACKAGE_RELEASE=
PACKAGERS=

usage() {
    cat <<'EOF'
Usage: scripts/build-packages.sh [options]

Build native throttled packages with nFPM.

Options:
  --output-dir DIR       Directory where packages are written (default: dist)
  --version VERSION      Package version (default: project version)
  --release RELEASE      Package release (default: 1, or 0.git.<sha> for local builds)
  --packager FORMAT      Package format: deb, rpm, or apk (repeatable; default: all)
  -h, --help             Show this help
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
            PACKAGE_VERSION=${2#v}
            shift 2
            ;;
        --release)
            [ "$#" -ge 2 ] || { echo "Missing value for --release" >&2; exit 2; }
            PACKAGE_RELEASE=$2
            shift 2
            ;;
        --packager)
            [ "$#" -ge 2 ] || { echo "Missing value for --packager" >&2; exit 2; }
            case "$2" in
                deb|rpm|apk) PACKAGERS="$PACKAGERS $2" ;;
                *) echo "Unsupported packager: $2" >&2; exit 2 ;;
            esac
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

if ! command -v nfpm >/dev/null 2>&1; then
    echo "nFPM is required to build native packages: https://nfpm.goreleaser.com/install/" >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -z "$PACKAGE_VERSION" ]; then
    PACKAGE_VERSION=$(
        PYTHONPATH="$ROOT_DIR" python3 -c 'from throttled_version import __version__; print(__version__)'
    )
fi

if [ -z "$PACKAGE_RELEASE" ]; then
    if GIT_SHA=$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null); then
        PACKAGE_RELEASE="0.git.$GIT_SHA"
    else
        PACKAGE_RELEASE=1
    fi
fi

if [ -z "$PACKAGERS" ]; then
    PACKAGERS=" deb rpm apk"
fi

BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/throttled-packages.XXXXXX")
trap 'rm -rf "$BUILD_ROOT"' EXIT HUP INT TERM

PACKAGE_ROOT="$BUILD_ROOT/root"
"$ROOT_DIR/scripts/stage-package.sh" --with-openrc "$PACKAGE_ROOT"
export PACKAGE_ROOT PACKAGE_VERSION PACKAGE_RELEASE

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(CDPATH= cd -- "$OUTPUT_DIR" && pwd)

for PACKAGER in $PACKAGERS; do
    case "$PACKAGER" in
        deb)
            TARGET="$OUTPUT_DIR/throttled_${PACKAGE_VERSION}-${PACKAGE_RELEASE}_all.deb"
            ;;
        rpm)
            TARGET="$OUTPUT_DIR/throttled-${PACKAGE_VERSION}-${PACKAGE_RELEASE}.noarch.rpm"
            ;;
        apk)
            TARGET="$OUTPUT_DIR/throttled_${PACKAGE_VERSION}-${PACKAGE_RELEASE}_noarch.apk"
            ;;
    esac

    (
        cd "$ROOT_DIR"
        nfpm package \
            --config "$ROOT_DIR/nfpm.yaml" \
            --packager "$PACKAGER" \
            --target "$TARGET"
    )
    echo "$TARGET"
done
