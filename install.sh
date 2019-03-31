#!/bin/sh

INSTALL_DIR="/opt/throttled"

if pidof systemd 2>&1 1>/dev/null; then
    systemctl stop throttled.service &>/dev/null
elif pidof runit 2>&1 1>/dev/null; then
    sv down throttled &>/dev/null
fi

mkdir -p "$INSTALL_DIR" &>/dev/null
set -e

cd "$(dirname "$0")"

echo "Copying config file..."
if [ ! -f /etc/throttled.conf ]; then
	cp etc/throttled.conf /etc
else
	echo "Config file already exists, skipping."
fi

if pidof systemd 2>&1 1>/dev/null; then
    echo "Copying systemd service file..."
    cp systemd/throttled.service /etc/systemd/system
elif pidof runit 2>&1 1>/dev/null; then
    echo "Copying runit service file"
    cp -R runit/throttled /etc/sv/
fi

echo "Building virtualenv..."
cp -n requirements.txt throttled.py mmio.py "$INSTALL_DIR"
cd "$INSTALL_DIR"
/usr/bin/python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt

if pidof systemd 2>&1 1>/dev/null; then
    echo "Enabling and starting systemd service..."
    systemctl daemon-reload
    systemctl enable throttled.service
    systemctl restart throttled.service
elif pidof runit 2>&1 1>/dev/null; then
    echo "Enabling and starting runit service..."
    ln -sv /etc/sv/throttled /var/service/
    sv up throttled
fi

echo "All done."
