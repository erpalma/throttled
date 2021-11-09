#!/bin/sh

LEGACY_INSTALL_DIR="/opt/lenovo_fix"
INSTALL_DIR="/opt/throttled"

if pidof systemd 2>&1 1>/dev/null; then
    systemctl stop lenovo_fix.service >/dev/null 2>&1
    systemctl stop throttled.service >/dev/null 2>&1
elif pidof runit 2>&1 1>/dev/null; then
    sv down lenovo_fix >/dev/null 2>&1
    sv down throttled >/dev/null 2>&1
elif pidof openrc 2>&1 1>/dev/null; then
    rc-service lenovo_fix stop >/dev/null 2>&1
    rc-service throttled stop >/dev/null 2>&1
fi

mv "$LEGACY_INSTALL_DIR" "$INSTALL_DIR" >/dev/null 2>&1
rm "$INSTALL_DIR/lenovo_fix.py" >/dev/null 2>&1
mkdir -p "$INSTALL_DIR" >/dev/null 2>&1
set -e

cd "$(dirname "$0")"

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

if pidof systemd 2>&1 1>/dev/null; then
    echo "Copying systemd service file"
    cp systemd/throttled.service /etc/systemd/system
    rm /etc/systemd/system/lenovo_fix.service >/dev/null 2>&1 || true
elif pidof runit 2>&1 1>/dev/null; then
    echo "Copying runit service file"
    cp -R runit/throttled /etc/sv/
    rm -r /etc/sv/lenovo_fix >/dev/null 2>&1 || true
elif pidof openrc-init 2>&1 1>/dev/null; then
    echo "Copying OpenRC service file"
    cp -R openrc/throttled /etc/init.d/throttled
    rm /etc/init.d/lenovo_fix >/dev/null 2>&1 || true
    chmod 755 /etc/init.d/throttled
fi

echo "Copying core files"
cp requirements.txt throttled.py mmio.py "$INSTALL_DIR"
echo "Building virtualenv"
cd "$INSTALL_DIR"
/usr/bin/python3 -m venv venv
. venv/bin/activate
pip install wheel
pip install -r requirements.txt

if pidof systemd 2>&1 1>/dev/null; then
    echo "Enabling and starting systemd service"
    systemctl daemon-reload
    systemctl enable throttled.service
    systemctl restart throttled.service
elif pidof runit 2>&1 1>/dev/null; then
    echo "Enabling and starting runit service"
    ln -sv /etc/sv/throttled /var/service/
    sv up throttled
elif pidof openrc-init 2>&1 1>/dev/null; then
    echo "Enabling and starting OpenRC service"
    rc-update add throttled default
    rc-service throttled start
fi

echo "All done."
