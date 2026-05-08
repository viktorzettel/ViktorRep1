#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${OUT:-$ROOT/kou_vps_bundle.tar.gz}"

cd "$ROOT"

tar -czf "$OUT" \
  --exclude='.env' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='data/live_capture/20*T*Z' \
  --exclude='data/live_capture/forensic_analysis' \
  --exclude='tools/tmp' \
  requirements.txt \
  .env.example \
  kou_dual_compact_web.py \
  kou_dual_compact_monitor.py \
  kou_live_capture.py \
  kou_polymarket_live_capture.py \
  polymarket_token_sniper.py \
  KOU_PRODUCTION_FINISH_LINE.md \
  KOU_DUAL_COMPACT_WEB_PRODUCTION_ROADMAP.md \
  KOU_RECOVERY_MANIFEST.md \
  analysis/autoresearch_kou \
  data/analysis_output_5m_microstructure/late_window_safety_thresholds.csv \
  tests/test_polymarket_token_sniper.py \
  tests/test_kou_polymarket_live_capture.py \
  deployment/vps

echo "$OUT"
