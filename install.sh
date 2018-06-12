#!/bin/sh

INSTALL_DIR="/opt/lenovo_fix"

systemctl stop lenovo_fix.service &>/dev/null

mkdir -p "$INSTALL_DIR" &>/dev/null
set -e

cd "$(dirname "$0")"

echo "Copying config file..."
if [ ! -f /etc/lenovo_fix.conf ]; then
	cp etc/lenovo_fix.conf /etc
else
	echo "Config file already exists, skipping."
fi

echo "Copying systemd service file..."
cp systemd/lenovo_fix.service /etc/systemd/system

echo "Building virtualenv..."
cp requirements.txt lenovo_fix.py "$INSTALL_DIR"
cd "$INSTALL_DIR"
virtualenv -p /usr/bin/python3 venv
. venv/bin/activate
pip install -r requirements.txt

echo "Enabling and starting systemd service..."
systemctl daemon-reload
systemctl enable lenovo_fix.service
systemctl restart lenovo_fix.service

echo "All done."
