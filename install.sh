#!/bin/sh

INSTALL_DIR="/opt/lenovo_fix"

if pidof systemd 2>&1 1>/dev/null; then
    systemctl stop lenovo_fix.service >/dev/null 2>&1
elif pidof runit 2>&1 1>/dev/null; then
    sv down lenovo_fix >/dev/null 2>&1
elif pidof openrc 2>&1 1>/dev/null; then
    rc-service lenovo_fix stop >/dev/null 2>&1
fi

mkdir -p "$INSTALL_DIR" >/dev/null 2>&1
set -e

cd "$(dirname "$0")"

echo "Copying config file..."
if [ ! -f /etc/lenovo_fix.conf ]; then
	cp etc/lenovo_fix.conf /etc
else
	echo "Config file already exists, skipping."
fi

if pidof systemd 2>&1 1>/dev/null; then
    echo "Copying systemd service file..."
    cp systemd/lenovo_fix.service /etc/systemd/system
elif pidof runit 2>&1 1>/dev/null; then
    echo "Copying runit service file"
    cp -R runit/lenovo_fix /etc/sv/
elif pidof openrc-init 2>&1 1>/dev/null; then
    echo "Copying OpenRC service file"
    cp -R openrc/lenovo_fix /etc/init.d/lenovo_fix
    chmod 755 /etc/init.d/lenovo_fix
fi

echo "Building virtualenv..."
cp -n requirements.txt lenovo_fix.py mmio.py "$INSTALL_DIR"
cd "$INSTALL_DIR"
/usr/bin/python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt

if pidof systemd 2>&1 1>/dev/null; then
    echo "Enabling and starting systemd service..."
    systemctl daemon-reload
    systemctl enable lenovo_fix.service
    systemctl restart lenovo_fix.service
elif pidof runit 2>&1 1>/dev/null; then
    echo "Enabling and starting runit service..."
    ln -sv /etc/sv/lenovo_fix /var/service/
    sv up lenovo_fix
elif pidof openrc-init 2>&1 1>/dev/null; then
    echo "Enabling and starting OpenRC service..."
    rc-update add lenovo_fix default
    rc-service lenovo_fix start
fi

echo "All done."
