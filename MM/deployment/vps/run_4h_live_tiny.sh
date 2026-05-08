#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(pwd)}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
PYTHON="${PYTHON:-$VENV_DIR/bin/python}"
PORT="${PORT:-8071}"
RUNTIME_SECONDS="${RUNTIME_SECONDS:-14400}"
SESSION_ID="${SESSION_ID:-$(date -u +"%Y%m%dT%H%M%SZ")}"
OUT_DIR="$APP_DIR/data/live_capture/$SESSION_ID"

if [ "${KOU_I_UNDERSTAND_REAL_MONEY:-}" != "YES" ]; then
  echo "Refusing to start live mode."
  echo "This can spend real pUSD. Re-run with KOU_I_UNDERSTAND_REAL_MONEY=YES only for a supervised tiny live test."
  exit 2
fi

cd "$APP_DIR"
mkdir -p "$OUT_DIR"
echo "$SESSION_ID" > "$APP_DIR/.kou_current_session"

cleanup() {
  set +e
  for pid_file in "$OUT_DIR/web.pid" "$OUT_DIR/browser.pid" "$OUT_DIR/capture.pid"; do
    if [ -s "$pid_file" ]; then
      kill "$(cat "$pid_file")" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT

echo "Starting VPS 4h TINY LIVE session: $SESSION_ID"
echo "Output: $OUT_DIR"
echo "Hard caps: 1 pUSD/order, 4 pUSD/session, 4 orders/session, FOK only"

"$PYTHON" kou_dual_compact_web.py \
  --port "$PORT" \
  --symbols xrpusdt \
  --display-source-overrides xrpusdt=browser-poly-chainlink \
  --model-source-overrides xrpusdt=browser-poly-chainlink \
  > "$OUT_DIR/web.log" 2>&1 &
echo $! > "$OUT_DIR/web.pid"

sleep 5

"$PYTHON" deployment/vps/headless_browser_keepalive.py \
  --url "http://127.0.0.1:$PORT/" \
  --api-url "http://127.0.0.1:$PORT/api/snapshot" \
  --check-seconds 10 \
  --max-runtime-seconds "$RUNTIME_SECONDS" \
  > "$OUT_DIR/headless_browser.log" 2>&1 &
echo $! > "$OUT_DIR/browser.pid"

sleep 10

"$PYTHON" kou_polymarket_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --assets xrp \
  --shadow-candidate analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py \
  --sniper-mode live \
  --sniper-live-ack \
  --sniper-order-size 1 \
  --sniper-max-order-cost 1 \
  --sniper-max-session-cost 4 \
  --sniper-max-session-orders 4 \
  --sniper-order-type FOK \
  --sniper-require-geoblock-clear \
  --max-runtime-seconds "$RUNTIME_SECONDS" \
  > "$OUT_DIR/polymarket_capture.log" 2>&1 &
echo $! > "$OUT_DIR/capture.pid"

wait "$(cat "$OUT_DIR/capture.pid")"
echo "Capture stopped. Cleaning up web/headless processes."
cleanup

"$PYTHON" deployment/vps/summarize_session.py "$OUT_DIR" | tee "$OUT_DIR/vps_health_summary.txt"
echo "Done. Summary: $OUT_DIR/vps_health_summary.txt"
