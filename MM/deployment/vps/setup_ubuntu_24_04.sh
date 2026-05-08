#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/kou-bot}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"

echo "Setting up Kou bot VPS runtime"
echo "APP_DIR=$APP_DIR"

sudo apt-get update
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  curl \
  jq \
  git \
  ca-certificates \
  chrony \
  unzip

mkdir -p "$APP_DIR/data/live_capture" "$APP_DIR/data/analysis_output_5m_microstructure"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip wheel setuptools
"$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/deployment/vps/requirements-vps.txt"

"$VENV_DIR/bin/python" -m playwright install --with-deps chromium

sudo systemctl enable --now chrony || true

echo
echo "Setup complete."
echo "Next:"
echo "1. Put your .env into $APP_DIR/.env"
echo "2. Run: cd $APP_DIR && bash deployment/vps/run_4h_dry_run.sh"
