#!/usr/bin/env bash
# Zip the Roku channel and (optionally) sideload it to a Roku in developer mode.
#   bash scripts/roku-deploy.sh                      -> builds dist/pi-iptv-dvr.zip only
#   ROKU_IP=192.168.1.20 ROKU_PASS=xxxx bash scripts/roku-deploy.sh   -> build + install
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO_DIR/dist/pi-iptv-dvr.zip"
mkdir -p "$REPO_DIR/dist"
rm -f "$OUT"
( cd "$REPO_DIR/roku" && zip -q -r "$OUT" manifest source components images )
echo "Built $OUT"

if [[ -n "${ROKU_IP:-}" ]]; then
  : "${ROKU_PASS:?set ROKU_PASS to the dev-mode password}"
  echo "Installing to Roku at $ROKU_IP ..."
  curl -sS --digest -u "rokudev:$ROKU_PASS" \
    -F "mysubmit=Install" -F "archive=@$OUT" \
    "http://$ROKU_IP/plugin_install" | grep -oE "(Install Success|Identical|Failed[^<]*|Error[^<]*)" || true
  echo "Debug console: telnet $ROKU_IP 8085"
fi
