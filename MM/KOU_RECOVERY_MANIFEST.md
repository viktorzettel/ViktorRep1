# Kou Recovery Manifest

Last updated: 2026-05-04

This file lists the minimum project state needed to recover the current XRP Kou/Polymarket research stack if the laptop is lost.

## Core Docs

- `KOU_PRODUCTION_FINISH_LINE.md` - concise production-readiness state, blockers, and next steps.
- `KOU_DUAL_COMPACT_WEB_PRODUCTION_ROADMAP.md` - long evidence chain and historical decisions.

## Core Runtime

- `kou_dual_compact_monitor.py` - shared Kou monitor primitives.
- `kou_dual_compact_web.py` - live browser dashboard and source bridge.
- `kou_live_capture.py` - Kou snapshot capture sidecar.
- `kou_polymarket_live_capture.py` - Polymarket market/quote/shadow execution capture sidecar.
- `polymarket_token_sniper.py` - dry-run foundation for future execution.
- `xrp_live_price_web.py` - lightweight Coinbase vs Poly/Chainlink visual source page.
- `xrp_source_alignment_capture.py` - source alignment capture helper.
- `price_source_probe.py` and `price_source_probe_web.py` - source probing helpers.

## Research / Replay

- `analysis/replay_shadow_execution.py` - historical/live replay harness.
- `analysis/autoresearch_kou/` - current candidates, generated reports, and candidate results.
- `analysis/analyze_live_capture_sessions.py`
- `analysis/analyze_polymarket_grid_signals.py`
- `analysis/analyze_reversal_regime_vetoes.py`
- `analysis/build_late_window_safety_policy.py`
- `analysis/compile_live_capture_forensic_report.py`
- `analysis/eth_xrp_5m_microstructure_analyzer.py`
- `analysis/eth_xrp_5m_safety_analyzer.py`
- `analysis/plot_5m_microstructure_insights.py`
- `analysis/plot_5m_safety_insights.py`
- `analysis/smoke_test_capture_pipeline.py`
- `analysis/summarize_live_policy_disagreements.py`
- `analysis/view_capture_health.py`

## Current Best Candidates

- Active pointer still points to `xrp_only_cap98_required_context_v2`.
- Best research safety candidate: `analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_conservative.py`.
- Best production-realism challenger: `analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py`.

## Compact Evidence

- `data/live_capture/forensic_analysis/` contains compact replay reports and forensic summaries.
- Raw captures under `data/live_capture/<session>/` are intentionally not part of the minimum GitHub backup because the folder is multi-GB. If raw data is needed, archive it separately.

## Validation

Known passing checks before backup:

```bash
python3 -m py_compile kou_dual_compact_web.py kou_polymarket_live_capture.py analysis/replay_shadow_execution.py polymarket_token_sniper.py
python3 -m pytest tests/test_kou_polymarket_live_capture.py tests/test_autoresearch_kou.py
```
