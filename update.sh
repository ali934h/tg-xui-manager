#!/usr/bin/env bash
# Pull the latest code and restart the service.
set -e

INSTALL_DIR="/root/tg-xui-manager"
SERVICE_NAME="tg-xui-manager"

echo "Pulling latest code…"
git -C "$INSTALL_DIR" pull --ff-only

echo "Updating Python dependencies…"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade -r "$INSTALL_DIR/requirements.txt"

echo "Restarting service…"
systemctl restart "$SERVICE_NAME"

echo "Done. Live logs:"
journalctl -u "$SERVICE_NAME" -f --no-pager
