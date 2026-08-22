#!/bin/sh
set -eu

LEGACY_INSTALL_DIR="/opt/lenovo_fix"
INSTALL_DIR="/opt/throttled"
INIT=auto
START_SERVICE=yes

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Install throttled from source into /opt/throttled.

Options:
  --init NAME    Service manager: auto, systemd, openrc, or runit (default: auto)
  --no-start     Install files without enabling or starting the service
  -h, --help     Show this help
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --init)
            [ "$#" -ge 2 ] || { echo "Missing value for --init" >&2; exit 2; }
            INIT=$2
            shift 2
            ;;
        --no-start)
            START_SERVICE=no
            shift
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

detect_init() {
    if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        echo systemd
    elif command -v rc-service >/dev/null 2>&1 && command -v rc-update >/dev/null 2>&1; then
        echo openrc
    elif command -v sv >/dev/null 2>&1; then
        echo runit
    else
        echo unknown
    fi
}

if [ "$INIT" = auto ]; then
    INIT=$(detect_init)
fi

case "$INIT" in
    systemd|openrc|runit) ;;
    *)
        echo "Unable to detect a supported service manager; use --init systemd, openrc, or runit." >&2
        exit 1
        ;;
esac

if [ "$INIT" = "systemd" ]; then
    systemctl stop lenovo_fix.service >/dev/null 2>&1 || true
    systemctl stop throttled.service >/dev/null 2>&1 || true
elif [ "$INIT" = "runit" ]; then
    sv down lenovo_fix >/dev/null 2>&1 || true
    sv down throttled >/dev/null 2>&1 || true
elif [ "$INIT" = "openrc" ]; then
    rc-service lenovo_fix stop >/dev/null 2>&1 || true
    rc-service throttled stop >/dev/null 2>&1 || true
fi

if [ -d "$LEGACY_INSTALL_DIR" ] && [ ! -e "$INSTALL_DIR" ]; then
    mv "$LEGACY_INSTALL_DIR" "$INSTALL_DIR"
fi
rm -f "$INSTALL_DIR/lenovo_fix.py"
mkdir -p "$INSTALL_DIR"

cd "$(dirname "$0")"
ROOT_DIR=$(pwd)

if [ -f /etc/lenovo_fix.conf ]; then
    echo "Updating config filename"
    mv /etc/lenovo_fix.conf /etc/throttled.conf
fi
echo "Copying config file"
if [ ! -f /etc/throttled.conf ]; then
    cp etc/throttled.conf /etc
else
    echo "Config file already exists, skipping"
fi

if [ "$INIT" = "systemd" ]; then
    echo "Copying systemd service file"
    cp systemd/throttled.service /etc/systemd/system
    rm -f /etc/systemd/system/lenovo_fix.service
elif [ "$INIT" = "runit" ]; then
    echo "Copying runit service file"
    cp -R runit/throttled /etc/sv/
    if [ -e /etc/sv/lenovo_fix ]; then
        rm -r /etc/sv/lenovo_fix
    fi
elif [ "$INIT" = "openrc" ]; then
    echo "Copying OpenRC service file"
    cp -R openrc/throttled /etc/init.d/throttled
    rm -f /etc/init.d/lenovo_fix
    chmod 755 /etc/init.d/throttled
fi

echo "Building virtualenv"
python3 -m venv "$INSTALL_DIR/venv"
echo "Installing throttled and its Python dependencies"
"$INSTALL_DIR/venv/bin/python3" -m pip install "$ROOT_DIR"
rm -f "$INSTALL_DIR/requirements.txt" "$INSTALL_DIR/throttled.py" "$INSTALL_DIR/mmio.py"

if [ "$START_SERVICE" = no ]; then
    if [ "$INIT" = systemd ]; then
        systemctl daemon-reload || true
    fi
    echo "Installed without enabling or starting the service."
elif [ "$INIT" = "systemd" ]; then
    echo "Enabling and starting systemd service"
    systemctl daemon-reload
    systemctl enable throttled.service
    systemctl restart throttled.service
elif [ "$INIT" = "runit" ]; then
    echo "Enabling and starting runit service"
    if [ ! -e /var/service/throttled ]; then
        ln -sv /etc/sv/throttled /var/service/
    fi
    sv up throttled
elif [ "$INIT" = "openrc" ]; then
    echo "Enabling and starting OpenRC service"
    rc-update add throttled default
    rc-service throttled start
fi

echo "All done."
