#!/usr/bin/env bash
# One-shot installer for Raspberry Pi OS (Bookworm/Bullseye, 32 or 64-bit).
# Usage:  bash scripts/install-pi.sh [port]
set -euo pipefail

PORT="${1:-8080}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_DIR="$REPO_DIR/server"
RUN_USER="$(id -un)"

echo "==> Installing system packages (python3, ffmpeg)"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip ffmpeg

echo "==> Creating Python virtualenv"
python3 -m venv "$SERVER_DIR/venv"
"$SERVER_DIR/venv/bin/pip" install -q --upgrade pip
"$SERVER_DIR/venv/bin/pip" install -q -r "$SERVER_DIR/requirements.txt"

echo "==> Installing systemd service (pi-iptv-dvr)"
sudo tee /etc/systemd/system/pi-iptv-dvr.service >/dev/null <<EOF
[Unit]
Description=Pi IPTV DVR server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$SERVER_DIR
Environment=IPTV_DATA_DIR=$SERVER_DIR/data
ExecStart=$SERVER_DIR/venv/bin/python app.py --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now pi-iptv-dvr

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Done. Open http://$IP:$PORT in a browser, go to Settings, paste your M3U + EPG URLs."
echo "In the Roku channel enter:  $IP:$PORT"
echo "Logs:  journalctl -u pi-iptv-dvr -f"
