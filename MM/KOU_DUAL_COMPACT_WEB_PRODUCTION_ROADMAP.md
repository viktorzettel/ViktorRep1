# Kou Dual Compact Web Production Roadmap

This file is a handoff note for future AI or human contributors working on [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) and [kou_dual_compact_monitor.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_monitor.py).

## Goal

Make the bot production ready for 5-minute binary crypto markets on ETH and XRP.

The bot is used for real directional decisions:
- buy `YES` if the market should settle above strike
- buy `NO` if the market should settle below strike
- avoid trades when the regime is unsafe

The key requirement is not just high model confidence. It is:
- calibrated probabilities
- reliable feed handling
- a strong independent safety layer
- a willingness to abstain

## Starting Point

Before the recent changes, the system had these main characteristics:

1. The Kou calibrator in [kou_dual_compact_monitor.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_monitor.py) used a fixed `2.0 sigma` jump threshold.
2. The web dashboard safety layer was a `trade score` in [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) that:
   - refreshed only once per minute
   - partly rewarded the Kou model for agreeing with itself
   - used `kou_yes` clarity as an input, so it was not an independent oracle
3. Warm-up handling was too permissive:
   - the model started calibrating at 31 candles
   - once Kou parameters existed, the system could effectively behave as if Kou were ready
4. ETH browser-side Polymarket reconnect behavior was too slow and was already improved in an earlier step:
   - websocket watchdog and reconnect path now recycle much faster after short connectivity loss

## Changes Already Made

### 1. Less aggressive jump detection in calibration

In [kou_dual_compact_monitor.py:46](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_monitor.py#L46) the fixed jump cutoff was raised and then adjusted again:

- old: `JUMP_THRESHOLD_SIGMA = 2.0`
- interim first pass: `JUMP_THRESHOLD_SIGMA = 3.5`
- current: `JUMP_THRESHOLD_SIGMA = 3.0`

Reason:
- `2 sigma` on short-horizon crypto data over-flags normal heavy-tailed noise as jumps
- `3.5 sigma` was likely too blunt for 10-second crypto moves
- `3.0 sigma` is the current interim compromise until a jump-robust estimator is implemented

### 2. Safety layer no longer updates only once per minute

The old minute-cache logic was removed from the safety calculation in [kou_dual_compact_web.py:415](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py#L415).

Effect:
- safety now recomputes whenever snapshots update
- the dashboard is no longer stuck with a stale safety view for up to 60 seconds

### 3. Safety layer is more independent from Kou

The safety calculation in [kou_dual_compact_web.py:415](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py#L415) was rewritten to focus more on market structure and less on Kou confidence.

Current safety inputs include:
- data freshness
- warm-up/readiness
- margin to strike in volatility units
- volatility regime
- volatility stability
- jump risk
- sign-flip / chop risk
- reversal pressure against the current side
- short-horizon trend cleanliness

Important:
- safety no longer uses `kou_yes` clarity as a core scoring input
- safety no longer gets a bonus just because the Kou signal is armed

### 4. Added helper metrics for short-horizon safety

New helpers were added in [kou_dual_compact_web.py:121](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py#L121) through [kou_dual_compact_web.py:201](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py#L177):

- `_jump_rate(...)` with a stricter default threshold
- `_robust_sigma(...)`
- `_sign_flip_rate(...)`
- `_adverse_share(...)`
- `_margin_safety_score(...)`
- `_kou_blend_weight(...)`

### 5. Kou warm-up is now blended instead of trusted immediately

In [kou_dual_compact_web.py:639](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py#L639):

- `raw_kou_yes` is computed when Kou params exist
- `kou_yes` shown to the system is now:
  - `kou_weight * raw_kou_yes + (1 - kou_weight) * bs_yes`
- `signal_ready` is only `True` when `kou_weight >= 0.999`

Meaning:
- warm Kou is not treated as fully trusted
- the system gradually transitions from BS-like behavior to full Kou behavior

### 6. Signals only arm when Kou is fully mature

In [kou_dual_compact_web.py:372](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py#L372), `_update_signal(...)` now depends on `signal_ready` instead of merely checking whether the model string is `KOU`.

Effect:
- no `BUY_YES` / `BUY_NO` signal should arm during warm-up

### 7. UI terminology now reflects intent better

The dashboard label was changed from `Trade Score` to `Safety` in [kou_dual_compact_web.py:1748](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py#L1748).

`KOU-WARM` is also shortened to `KOU~` in the pill display so the narrow layout stays readable.

### 8. A reusable historical safety analyzer was added

A new script was added at [analysis/eth_xrp_5m_safety_analyzer.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/eth_xrp_5m_safety_analyzer.py).

What it does:
- ingests historical candles for ETH/XRP via `asset=path` inputs
- derives snapshot-level features inside each 5-minute bucket
- computes reversal tables by `asset x vol_regime x time_left_s x delta_bin_bps`
- computes hour-of-week priors
- computes hour-of-week plus time-left plus regime priors
- emits candidate no-go zones based on reversal upper confidence bound, jumpiness, and chop

Current outputs when run on real data:
- `snapshot_features.csv`
- `reversal_by_delta_regime.csv`
- `hour_of_week_priors.csv`
- `hour_timeleft_regime_priors.csv`
- `candidate_no_go_zones.csv`
- `summary.json`

Important:
- this is the first real artifact for Phase 1
- it does not yet calibrate Kou probabilities
- it does not yet use settlement-source alignment
- it is a historical analysis entry point, not yet wired into the live dashboard

### 9. Historical 1-minute datasets were downloaded into the repo

Downloaded files in the MM root:

- [ethusd_1m_coinbase_6m.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/ethusd_1m_coinbase_6m.csv)
- [xrpusd_1m_coinbase_6m.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/xrpusd_1m_coinbase_6m.csv)

Current status:
- both files are about 6 months of 1-minute Coinbase candles
- ETH rows: `261,683`
- XRP rows: `261,668`

Important caveat:
- XRP Coinbase is acceptable for now because the live XRP model already uses Coinbase USD
- ETH Coinbase is only a temporary historical dataset for now
- ETH in the live web bot uses the browser Polymarket/Chainlink path, so exact historical source alignment for ETH is still an open task

The downloader added for repeatability is:

- [coinbase_download_1m.py](/Users/viktorzettel/Downloads/ViktorAI/MM/coinbase_download_1m.py)

### 10. First ETH/XRP historical safety tables were generated

The analyzer has now been run on the downloaded Coinbase datasets:

```bash
python3 analysis/eth_xrp_5m_safety_analyzer.py \
  --inputs ethusdt=ethusd_1m_coinbase_6m.csv,xrpusdt=xrpusd_1m_coinbase_6m.csv \
  --output-dir data/analysis_output_5m_safety
```

Output directory:

- [data/analysis_output_5m_safety](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety)

Generated files:

- [snapshot_features.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/snapshot_features.csv)
- [reversal_by_delta_regime.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/reversal_by_delta_regime.csv)
- [hour_of_week_priors.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/hour_of_week_priors.csv)
- [hour_timeleft_regime_priors.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/hour_timeleft_regime_priors.csv)
- [candidate_no_go_zones.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/candidate_no_go_zones.csv)
- [summary.json](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/summary.json)

Current summary from `summary.json`:

- ETH rows analyzed: `261,682`
- XRP rows analyzed: `261,667`
- ETH reversal table rows: `302`
- XRP reversal table rows: `326`
- ETH candidate no-go rows: `1,803`
- XRP candidate no-go rows: `1,853`

Interpretation:

- we now have first-generation historical safety priors for both assets
- these are usable for inspection and threshold tuning
- they are not yet production no-go rules

Important caveats:

- ETH analysis is still based on Coinbase history, not exact Polymarket/Chainlink-aligned history
- the candidate no-go table is currently broad and likely over-inclusive
- the current analyzer uses close snapshots and heuristic jump/chop proxies
- Kou probability calibration is still not included

One implementation bug was found and fixed while running this step:

- the analyzer originally inferred bar size incorrectly for microsecond-resolution datetimes
- `infer_bar_seconds(...)` was fixed so the Coinbase files run correctly

### 11. Stronger veto-zone tables were added and generated

The analyzer was extended to add a narrower, more conservative veto-layer output on top of the first broad no-go table.

New analyzer additions:

- `hour_utc` is now part of the derived snapshot features
- hour-of-day plus time-left plus regime priors are now exported
- a new `strong_veto_zones.csv` table is produced using stricter sample and fragility filters

New files now generated in [data/analysis_output_5m_safety](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety):

- [hour_of_day_timeleft_regime_priors.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/hour_of_day_timeleft_regime_priors.csv)
- [strong_veto_zones.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/strong_veto_zones.csv)

Current summary after rerunning the analyzer:

- ETH candidate no-go rows: `1,803`
- XRP candidate no-go rows: `1,853`
- ETH strong veto rows: `50`
- XRP strong veto rows: `42`

Interpretation:

- the original candidate table is still useful for exploration and threshold tuning
- the new strong-veto table is much smaller and more plausible as the basis for production veto logic
- this is still not final production logic yet because it has not been walk-forward validated and is still based on Coinbase ETH history

Current strong-veto design:

- minimum cell sample size: `500`
- reversal rate gate: `>= 0.30`
- reversal upper-95 gate: `>= 0.34`
- median absolute delta-to-strike gate: `<= 8 bps`
- optional jump/chop fragility overlays remain available in the analyzer outputs

This step completed the first reduction from a very broad no-go candidate space into a smaller shortlist of historically fragile regimes.

### 12. First probability-calibration tables were added and generated

The analyzer was extended beyond regime discovery into first-pass probability calibration for the live decision stack.

New outputs now generated in [data/analysis_output_5m_safety](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety):

- [pipeline_snapshot_probs.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/pipeline_snapshot_probs.csv)
- [probability_calibration_by_bin.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/probability_calibration_by_bin.csv)
- [probability_calibration_by_regime.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/probability_calibration_by_regime.csv)
- [probability_calibration_summary.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_safety/probability_calibration_summary.csv)

What this step does:

- computes a historical `bs_yes` probability
- computes a rolling `raw_kou_yes` proxy probability using the current Kou-style calibration logic
- computes a `blended_kou_yes` proxy using the same warm-up blend shape as the live bot
- compares those historical probabilities to realized 5-minute outcomes

Important caveat:

- this is still a `1-minute proxy` study, not an exact replay of the live `10-second` pipeline
- it is good enough to expose broad miscalibration and regime problems
- it is not the final production validation artifact

Current calibration summary:

- ETH blended Kou proxy:
  - weighted absolute calibration gap: about `1.18%`
  - Brier mean: about `0.1647`
  - directional win rate: about `74.87%`
- ETH BS:
  - weighted absolute calibration gap: about `2.91%`
  - Brier mean: about `0.1628`
  - directional win rate: about `75.60%`
- XRP blended Kou proxy:
  - weighted absolute calibration gap: about `1.01%`
  - Brier mean: about `0.1608`
  - directional win rate: about `75.87%`
- XRP BS:
  - weighted absolute calibration gap: about `2.59%`
  - Brier mean: about `0.1588`
  - directional win rate: about `76.48%`

Interpretation:

- under this current `1-minute proxy` study, the Kou-style probabilities are better calibrated in the tails than plain BS
- however, BS still has slightly better overall Brier score and slightly better directional hit rate
- this means the current Kou calibration is not yet clearly superior in full pipeline performance
- it may be smoothing probability tails more honestly, but it is not yet winning the larger production test

Important tail insight from `probability_calibration_by_bin.csv`:

- ETH blended Kou proxy in the `0.95 to 1.00` bin realized about `97.41%`
- XRP blended Kou proxy in the `0.95 to 1.00` bin realized about `97.34%`
- plain BS was much more overconfident in those extreme bins, especially near `0.95+`

Important regime insight from `probability_calibration_by_regime.csv`:

- the largest calibration gaps cluster in high-volatility regimes
- especially at `time_left_s = 240`
- this suggests that early-in-bucket high-volatility states are still where the model family is least trustworthy

This step answered an important question:

- we now have a way to test whether `90%` and `95%+` style probabilities behave like those numbers historically
- the answer is: partially yes for the Kou-style proxy, but not yet strongly enough to call the full pipeline production ready

### 13. Sub-minute data tooling was added and XRP Coinbase 1s history was downloaded

Because the `1-minute` datasets were too coarse for late-bucket reversal analysis, the workflow was upgraded toward sub-minute data.

New scripts added:

- [binance_download_1s.py](/Users/viktorzettel/Downloads/ViktorAI/MM/binance_download_1s.py)
- [coinbase_download_trades_to_1s.py](/Users/viktorzettel/Downloads/ViktorAI/MM/coinbase_download_trades_to_1s.py)
- [analysis/eth_xrp_5m_microstructure_analyzer.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/eth_xrp_5m_microstructure_analyzer.py)
- [analysis/plot_5m_microstructure_insights.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/plot_5m_microstructure_insights.py)

Purpose of the new workflow:

- rebuild `1-second` OHLC from real trades where needed
- analyze `15-second` snapshots inside each `5-minute` market
- measure near-strike reversal risk more granularly
- produce heatmaps for reversal risk, volatility, jumps, and chop that are more relevant to the live Kou bot

Downloaded dataset now present:

- [data/xrpusd_1s_coinbase_21d.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/xrpusd_1s_coinbase_21d.csv.gz)

Current XRP Coinbase 1s dataset stats:

- rows: `937,066` one-second buckets with trades
- span: `2026-03-14T21:00:01+00:00` to `2026-04-04T20:59:59+00:00`
- source trades aggregated: `3,838,356`
- pages fetched from Coinbase public trades endpoint: `3,839`

Important interpretation:

- this gives us real sub-minute XRP/USD market structure from Coinbase, which is much closer to the live XRP setup than the previous `1m` proxy
- the download step is complete
- the upgraded microstructure analysis was run later in a follow-up step on both XRP and ETH
- this section records the moment the XRP sub-minute dataset first became available

Important caveat:

- at the time of the first sub-minute download step, only XRP had matching `1s` history
- ETH was later downloaded too, so both assets now have Coinbase-based `1s` history in the workspace
- the next phase moved from "download" to actual microstructure analysis

### 14. ETH and XRP 1s microstructure analysis was run

The new microstructure workflow has now been executed on both sub-minute Coinbase datasets:

- [data/ethusd_1s_coinbase_21d.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/ethusd_1s_coinbase_21d.csv.gz)
- [data/xrpusd_1s_coinbase_21d.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/xrpusd_1s_coinbase_21d.csv.gz)

Output directory:

- [data/analysis_output_5m_microstructure](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure)

Generated tables:

- [ethusdt_1s_enriched.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/ethusdt_1s_enriched.csv.gz)
- [ethusdt_10s_candles.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/ethusdt_10s_candles.csv.gz)
- [xrpusdt_1s_enriched.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/xrpusdt_1s_enriched.csv.gz)
- [xrpusdt_10s_candles.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/xrpusdt_10s_candles.csv.gz)
- [snapshot_15s_features.csv.gz](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/snapshot_15s_features.csv.gz)
- [near_strike_heatmap.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/near_strike_heatmap.csv)
- [delta_timeleft_reversal.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/delta_timeleft_reversal.csv)
- [near_strike_timeleft_summary.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/near_strike_timeleft_summary.csv)
- [micro_veto_zones.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/micro_veto_zones.csv)
- [summary.json](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/summary.json)

Generated visuals:

- [micro_reversal_heatmap.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals/micro_reversal_heatmap.png)
- [micro_volatility_heatmap.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals/micro_volatility_heatmap.png)
- [micro_jump_heatmap.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals/micro_jump_heatmap.png)
- [micro_chop_heatmap.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals/micro_chop_heatmap.png)
- [micro_delta_timeleft_heatmap.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals/micro_delta_timeleft_heatmap.png)
- [micro_timeleft_summary.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals/micro_timeleft_summary.png)

Current summary highlights from `summary.json`:

- ETH near-strike late-90s reversal rate: about `19.87%`
- XRP near-strike late-90s reversal rate: about `18.33%`
- ETH worst reversal cell: `hour 22`, `285s left`, about `49.57%`
- XRP worst reversal cell: `hour 16`, `285s left`, about `49.12%`

Most important findings:

- the `1-second` analysis confirms the broad `1-minute` conclusion: reversal risk rises sharply the earlier we are in the bucket
- near-strike reversal risk is much lower in the last `15-30s` and much higher at `240-285s`
- for both ETH and XRP, the strongest danger still clusters near the start of the `5m` bucket, not near the very end
- near-strike adverse excursion also rises steadily as time-left increases, which means earlier entries have much more room to be hurt by reversals
- XRP shows higher median short-horizon realized volatility than ETH in the same near-strike windows
- ETH shows higher jump-in-the-last-30s rates than XRP under the current microstructure proxy

Important caveat:

- the current `micro_veto_zones.csv` output is too broad and is not yet trustworthy as a production veto layer
- under the current thresholds it effectively flags every `hour x time_left` cell, which means the thresholds need to be tightened or redesigned
- the microstructure analysis itself is useful, but the veto extraction logic still needs another iteration

### 15. Late-window danger tables were added to fit the live bot better

The microstructure analyzer was then extended in a second pass so the outputs match the actual live decision window more closely.

Why this was needed:

- the live bot only acts in the last `90s`
- raw `bps from strike` is not the best scale for short-horizon binary risk
- the first `micro_veto_zones.csv` output was far too broad to trust as a live veto layer

New analyzer additions:

- `margin_z` as volatility-scaled distance to strike
- `current_side` so danger can be measured separately for current `YES` versus `NO`
- `future_adverse_cross` so we measure whether the current side gets crossed against before expiry
- `time_to_first_adverse_cross_s`
- focused late-window policy tables for the last `90s`

New outputs now generated in [data/analysis_output_5m_microstructure](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure):

- [late_window_policy.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/late_window_policy.csv)
- [late_window_danger_zones.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/late_window_danger_zones.csv)

New visual now generated in [data/analysis_output_5m_microstructure/visuals](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals):

- [late_window_margin_heatmap.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/visuals/late_window_margin_heatmap.png)

Current summary from the updated `summary.json`:

- ETH late-window policy rows: `115`
- XRP late-window policy rows: `109`
- ETH late-window danger rows: `36`
- XRP late-window danger rows: `35`
- old broad micro-veto rows: `456` ETH and `456` XRP
- new late-window danger shortlist: `36` ETH and `35` XRP

Current worst late-window cells:

- ETH worst late-window policy cell:
  - `75s` left
  - current side `YES`
  - `margin_z_bin = 0.0`
  - reversal rate about `41.90%`
  - adverse-cross rate about `66.36%`
  - samples: `654`
- XRP worst late-window policy cell:
  - `15s` left
  - current side `YES`
  - `margin_z_bin = 0.0`
  - reversal rate about `44.14%`
  - adverse-cross rate about `47.75%`
  - samples: `222`

Interpretation:

- this is a much better direction than the original blanket `micro_veto_zones.csv` output
- the new tables focus on the exact late decision window that the live bot uses
- volatility-scaled strike distance is a more sensible measure than raw bps for binary expiry risk
- adverse-cross risk is more directly relevant than generic reversal counts, because it captures whether the live side gets crossed against before expiry

Important caveat:

- this is still not final production veto logic
- the new `late_window_danger_zones.csv` output is much tighter, but it still needs walk-forward validation and probably a split into `caution` versus `hard no-go`
- order-flow imbalance and aggressor-side pressure are still missing from the feature set

### 16. Draft late-window safety-policy tables were built

The next step after the late-window analysis was to convert those danger cells into something closer to live bot logic.

A new builder script was added:

- [analysis/build_late_window_safety_policy.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/build_late_window_safety_policy.py)

New outputs now generated in [data/analysis_output_5m_microstructure](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure):

- [late_window_policy_levels.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/late_window_policy_levels.csv)
- [late_window_safety_thresholds.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/late_window_safety_thresholds.csv)
- [late_window_safety_policy_summary.json](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/late_window_safety_policy_summary.json)

What this step does:

- classifies each late-window cell as `clear`, `caution`, or `hard_no_go`
- converts those classified cells into per-asset, per-time-left, per-side `margin_z` thresholds
- gives the live bot a first draft of how to turn historical microstructure risk into simple late-window policy bands

Current summary from `late_window_safety_policy_summary.json`:

- ETH policy rows:
  - `83` clear
  - `20` caution
  - `12` hard no-go
- XRP policy rows:
  - `80` clear
  - `18` caution
  - `11` hard no-go
- current maximum threshold shape:
  - `hard_no_go` reaches `margin_z_bin = 0.0`
  - `caution` reaches `margin_z_bin = 1.0`

Interpretation:

- this is the first policy artifact that starts to look like something the live bot can actually use
- the emerging structure is intuitive:
  - very near strike in the last `90s` is often `hard no-go`
  - slightly wider but still near strike is often `caution`
  - the strongest danger remains concentrated in small `margin_z` bins

Important caveat:

- this is still a draft threshold builder, not final production policy
- the thresholds are hand-tuned from current microstructure outputs, not yet walk-forward optimized
- the next step is to validate these policy bands against the live heuristic safety path and test whether they improve abstention quality

### 17. Draft late-window policy was softly wired into the live bot

The live safety path in [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) now loads the generated late-window threshold table and applies it as a soft overlay on top of the existing heuristic safety score.

What changed:

- [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) now loads [late_window_safety_thresholds.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/analysis_output_5m_microstructure/late_window_safety_thresholds.csv)
- it evaluates the policy only in the last `90s`
- it uses the current live side and volatility-scaled `margin_z`
- `HARD_NO_GO` now caps the live safety state to `AVOID`
- `CAUTION` now caps the live safety state to `CAREFUL`
- the dashboard now exposes:
  - `Late policy`
  - `Policy z`

Important design choice:

- the old heuristic safety score was not removed yet
- the new late-window policy is currently a soft overlay, not a full replacement
- this is deliberate, so we can compare policy behavior against the older heuristic before committing to a full swap

Current implementation meaning:

- historical microstructure risk is now starting to influence the live decision surface
- but the system still needs validation before this policy can be treated as the final production oracle

### 18. Heuristic-versus-policy comparison and disagreement logging were added

The next follow-up step was to make the comparison between the old heuristic safety score and the new late-window policy explicit.

What changed in [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py):

- the live snapshot now exposes both:
  - the base heuristic safety output
  - the final policy-adjusted safety output
- the dashboard now shows:
  - `Heuristic`
  - `Override`
- when the late-window policy actually changes the live safety result, the bot now writes a structured JSONL event log

New log file path:

- [data/live_policy_disagreements.jsonl](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_policy_disagreements.jsonl)

What gets logged:

- timestamp
- asset
- current price
- strike
- time left
- sample count
- base heuristic score, label, and reason
- final policy-adjusted score, label, and reason
- late-window policy level, reason, bucket, and `margin_z`

Why this matters:

- this is the first live audit trail for whether the new policy is actually doing useful work
- it lets future analysis answer:
  - when did policy override the heuristic?
  - was that override usually correct?
  - should `HARD_NO_GO` stay a soft cap or become a hard veto?

Important caveat:

- the log only records meaningful policy disagreements, not every snapshot
- this is intentional so the file stays focused on the cases that matter most for validation

### 19. A disagreement-summary helper was added

To make the new live disagreement log usable, a small summary tool was added:

- [analysis/summarize_live_policy_disagreements.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/summarize_live_policy_disagreements.py)

Purpose:

- read [data/live_policy_disagreements.jsonl](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_policy_disagreements.jsonl)
- summarize overrides by asset
- summarize overrides by late-window bucket
- summarize label transitions such as `GOOD -> AVOID`

Default output directory when run:

- [data/live_policy_disagreement_summary](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_policy_disagreement_summary)

This is mainly a support tool for the next validation phase once real live disagreement events have accumulated.

### 20. A sidecar live-capture logger was added

To build a real calibration dataset from the running bot, a separate sidecar logger was added:

- [kou_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_live_capture.py)

Purpose:

- poll the local [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) snapshot API
- capture the full live decision surface while the bot is running
- switch cadence automatically:
  - every `5s` outside the late window
  - every `1s` in the last `120s`
- write a reusable live dataset for calibration and policy analysis

Per-session outputs:

- `session_meta.json`
- `snapshots.jsonl`
- `events.jsonl`
- `bucket_outcomes.jsonl`

Default session root:

- [data/live_capture](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture)

What the logger captures per snapshot:

- market state
- Kou / BS model outputs
- signal state
- final safety output
- base heuristic safety output
- late policy output
- safety component breakdown
- jump-threshold sweeps for:
  - `2.0 sigma`
  - `2.5 sigma`
  - `3.0 sigma`
  - `3.5 sigma`

Why this matters:

- this is the missing dataset builder for live calibration
- it lets future analysis test:
  - which probability bands actually work
  - whether persistence rules like `2s` versus `4s` are better
  - whether `2.0`, `2.5`, `3.0`, or `3.5 sigma` behaves best
  - whether the safety policy is actually improving abstention quality

### 21. The live snapshot was enriched for calibration capture

To support the sidecar logger, [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) now exposes richer telemetry in the snapshot payload:

- `safety_components`
- `jump_sweep_10s_10m`
- `jump_sweep_30s_15m`

This means a single live run can now be analyzed later for multiple jump-threshold configurations without rerunning the market.

### 22. Live-capture data analysis workflow and current findings

Live-capture analysis is now the main bridge between the running bot and production calibration.

The purpose of this analysis is not just to summarize dashboard behavior. It should build an empirical dataset that answers:

- when the live pipeline shows a given probability, how often does that side actually settle correctly?
- which time-left windows are actually tradable?
- which persistence rules are better, such as `2s`, `4s`, or `6s` above threshold?
- whether Kou, BS, raw Kou, or blended Kou gives the better probability input under real live conditions
- whether the heuristic safety layer and historical late-window policy are separating good regimes from bad regimes
- whether `CAUTION` and `HARD_NO_GO` should remain soft caps or become true vetoes
- which jump-threshold sweep (`2.0`, `2.5`, `3.0`, `3.5 sigma`) best predicts danger

Current live-capture sessions present:

- [data/live_capture/20260414T161252Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260414T161252Z)
- [data/live_capture/20260417T060220Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260417T060220Z)
- [data/live_capture/20260417T134535Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260417T134535Z)
- [data/live_capture/20260418T210358Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260418T210358Z)

Per-session file meaning:

- `session_meta.json`: run metadata, source URL, cadence settings, git revision, start/stop timestamps
- `snapshots.jsonl`: one row per asset per capture tick; this is the main calibration dataset
- `events.jsonl`: signal changes, policy events, and other event-like state transitions
- `bucket_outcomes.jsonl`: one row per asset per 5-minute bucket with final settlement side and bucket-level summary stats

Important schema notes for future agents:

- snapshot fields are nested JSON; do not assume flat keys
- session timestamps live under `session.captured_at_ts` and `session.captured_at_iso`
- market side is `market.current_side` and is lowercase (`yes` / `no`) in snapshots, so normalize to uppercase before comparing to outcomes
- the signal is `signal.state`, not `signal.signal`
- safety is shaped as `safety.final_score`, `safety.final_label`, `safety.heuristic_score`, and `safety.heuristic_label`, not as nested `safety.final.score`
- the active model label is `model.model`; the warm-up phase is `model.kou_phase`
- volatility fields are `volatility.vol_30m_bp_1m` and `volatility.vol_1h_bp_1m`
- jump fields are `jumps.jump_10s_10m_rate`, `jumps.jump_30s_15m_rate`, plus the two jump-sweep dictionaries
- policy fields are `policy.level`, `policy.margin_z`, `policy.bucket_s`, `policy.override`, and `policy.reason`

Recommended analysis join:

- load `snapshots.jsonl`
- load `bucket_outcomes.jsonl`
- keep only bucket rows where `complete == true` and `settled_yes` is not null
- join snapshots to outcomes on `symbol` and `bucket_end`
- derive:
  - `settled_yes_num`
  - `pred_yes = kou_yes >= 0.5`
  - `pred_bs_yes = bs_yes >= 0.5`
  - `kou_brier`
  - `bs_brier`
  - `current_side_win`
  - `time_left` bins such as `0-15`, `15-30`, `30-60`, `60-90`, `90-120`, `120-180`, `180-240`, `240-300`
  - first signal per `symbol x bucket_end`

Important file distinction:

- [data/live_policy_disagreements.jsonl](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_policy_disagreements.jsonl) is a rolling cross-session disagreement log
- it should not be treated as belonging to only one capture session
- use it to study policy override patterns, not as a replacement for the full `snapshots.jsonl` plus `bucket_outcomes.jsonl` join

First analyzed session:

- [data/live_capture/20260414T161252Z/analysis/analysis_report.md](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260414T161252Z/analysis/analysis_report.md)
- [data/live_capture/20260414T161252Z/analysis/analysis_summary.json](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260414T161252Z/analysis/analysis_summary.json)
- [data/live_capture/20260414T161252Z/analysis/visuals](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260414T161252Z/analysis/visuals)

Current findings from the first analyzed live session:

- the dataset had `16,082` snapshots and `101` complete buckets after filtering outcomes
- the real edge was strongly late-window:
  - Kou accuracy at `240-300s` left: about `51.4%`
  - Kou accuracy at `0-15s` left: about `95.3%`
- first signal per bucket performed very strongly in that session:
  - `90` signaled buckets
  - about `97.78%` win rate
  - ETH first-signal win rate was `100%`
  - XRP first-signal win rate was about `95.56%`
- BS still beat Kou overall on raw snapshot scoring in that session:
  - Kou accuracy about `80.47%`
  - BS accuracy about `82.93%`
  - Kou Brier about `0.1296`
  - BS Brier about `0.1156`
- the late policy overlay appeared useful:
  - policy `CLEAR` snapshots had about `96.03%` current-side hold win
  - policy `CAUTION` snapshots had about `69.27%` current-side hold win
  - override snapshots had much worse hold quality than non-overrides
- `margin_z` was one of the strongest live safety features:
  - in the last `120s`, `margin_z < 0.5` held only about `62.46%`
  - in the last `120s`, `margin_z < 1.0` held only about `69.45%`

Interpretation for future agents:

- do not judge the bot by raw model accuracy across the whole 5-minute bucket
- the current design intentionally waits for the last `90s`, and the live capture supports that choice
- the actual production question is whether the selective signal plus safety/policy veto stack improves outcomes, not whether every snapshot is directionally predictive
- near-strike late-window states are the main danger zone; `margin_z` should remain a primary feature
- one session is not enough to finalize thresholds, so aggregate the later sessions before making hard production changes

Completed follow-up analysis step:

- build and run an aggregate live-capture analyzer across all sessions in [data/live_capture](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture)
- produce one combined report with per-session and all-session metrics
- compare first-signal results, safety labels, policy levels, margin-z buckets, time-left bins, and jump-sigma sweeps
- use the aggregate results as the baseline before promoting `CAUTION` or `HARD_NO_GO` from soft overlay toward hard veto behavior

### 23. Aggregate live-capture analysis was added and run

The aggregate analyzer is now available at:

- [analysis/analyze_live_capture_sessions.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/analyze_live_capture_sessions.py)

It was run across the four available sessions and wrote outputs to:

- [data/live_capture/aggregate_analysis](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/aggregate_analysis)
- [data/live_capture/aggregate_analysis/analysis_report.md](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/aggregate_analysis/analysis_report.md)
- [data/live_capture/aggregate_analysis/analysis_summary.json](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/aggregate_analysis/analysis_summary.json)

Current aggregate findings:

- joined snapshots: `59,897`
- complete non-flat buckets: `386`
- actual first-signal buckets: `333`
- actual first-signal wins: `327`
- actual first-signal win rate: about `98.2%`
- ETH first-signal win rate: `165/165`
- XRP first-signal win rate: `162/168`
- first-signal performance was strong in US-hours, Europe/pre-US, and weekend sessions
- BS still beats Kou on broad snapshot-level accuracy and Brier score
- the edge remains strongly late-window rather than full-bucket
- `GOOD` safety and `CLEAR` policy states separate much better hold quality than `OK` / `CAREFUL` / `CAUTION`
- `margin_z` remains the clearest simple danger feature
- all six actual first-signal losses were XRP buckets with multiple strike crosses after signal

Interpretation:

- the current live evidence supports the design direction: wait late, require persistence, and use safety/policy to avoid near-strike/choppy regimes
- it does not yet prove time-of-day superiority or production EV because the sample is still small and outcomes are feed-settled rather than actual filled Polymarket trades
- more targeted 4-hour sessions are still useful, especially balanced across US regular hours, Europe/pre-US, late US/off-hours, and weekend

### 24. ETH live-source testing was expanded

To help solve the ETH source-alignment problem, [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) was extended with runtime-switchable ETH sources beyond the original browser Polymarket/Chainlink path.

Added switchable sources:

- `coinbase-advanced-usd`
- `coinbase-usd`
- `kraken-usd`
- `gemini-usd`
- `bitstamp-usd`
- `okx-usdt`
- `pyth-usd`

Important current source state:

- both ETH and XRP are now settled on `coinbase-advanced-usd` as the default live source
- the decision to settle on Coinbase Advanced was made because it appeared closest in practice to the user-observed Polymarket price for both assets
- the browser Polymarket/Chainlink path for ETH appeared imperfect in practice and may sit about `$0.50-$1.00` away from the user-observed Polymarket price

Pyth-specific debugging status:

- the first `pyth-usd` implementation did not populate because the Hermes response handling was incomplete
- direct checks showed:
  - Pyth Hermes is reachable with browser-like headers
  - the endpoint returns either:
    - a direct `list` payload on `api/latest_price_feeds`
    - or a `dict` with `parsed` on `v2/updates/price/latest`
- the parser was then updated to handle both shapes

Meaning:

- if `pyth-usd` still shows no ETH in the UI after this fix, the next issue is no longer the basic endpoint format
- at that point it becomes a runtime/quality comparison problem rather than a simple broken parser

Current practical recommendation:

- the live source decision is now settled on Coinbase Advanced for both ETH and XRP
- the next immediate step is to run the settled source configuration together with [kou_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_live_capture.py)

## Current Warm-Up / Candle Behavior

These constants are still:

- `MIN_CALIB_CANDLES = 30`
- `FULL_CALIB_CANDLES = 60`

Interpretation:

1. Kou calibration can begin after `31 candles`
   - because calibration needs `MIN_CALIB_CANDLES + 1`
2. At `31 candles`, Kou is now only `warm`
   - not fully trusted
   - signal arming is still disabled
3. Full Kou trust happens at about `61 candles`
   - because warm-up weight ramps from candle `31` to candle `61`
   - `signal_ready` flips only when the blend weight is effectively `1.0`

In short:
- yes, Kou becomes `warm` at `31 candles`
- no, it is not considered fully ready there
- full readiness is about `61 candles`

## What Still Needs To Change Before Production

### Phase 1. Historical analyzer on the actual traded setup

This is the most important next step.

We now have a first implementation scaffold in [analysis/eth_xrp_5m_safety_analyzer.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/eth_xrp_5m_safety_analyzer.py).

What still needs to happen for Phase 1:
- the actual feed used for decisions
- the actual settlement proxy if direct settlement data is not available
- 10-second and 1-minute derived features

The completed analyzer should output:
- Kou probability calibration curves
- reversal probability by `time_left x distance_to_strike x vol_regime`
- jump hazard by regime
- hour-of-week regime priors
- feed disagreement / basis analysis

Required result:
- prove whether a displayed `90%+` probability corresponds to real out-of-sample win rates

### Phase 2. Replace heuristic jump detection with a jump-robust estimator

Current state:
- jump detection is still heuristic, just safer than before

Needed:
- realized bipower variation or truncated variation
- jump test or jump proxy normalized by local robust volatility
- separate continuous volatility estimate from jump estimate

Do not keep the fixed sigma heuristic as the final production version.

### Phase 3. Replace the heuristic safety score with a calibrated late-window hold-risk model

Current state:
- safety is still heuristic, but more sensible and more independent than before

Needed:
- a supervised or table-based late-window hold-risk model trained on historical outcomes
- features should include:
  - time left to expiry
  - distance to strike in volatility units
  - current live side (`YES` / `NO`)
  - future adverse-cross hazard
  - time to first adverse cross
  - short-horizon sign flips
  - realized semivariance
  - jump proxy
  - vol regime
  - hour-of-week
  - active-trading share / trade-count intensity
  - BTC co-move / cross-asset shock flags if useful

Production rule:
- use the safety layer as a veto or abstention oracle
- do not let Kou override a high reversal-risk regime

### Phase 4. Feed and settlement alignment

Need to answer clearly:
- what exact price decides the market settlement?
- is the bot decision feed the same feed?
- if not, what is the measurable basis risk?

If the model trades on one feed and the market resolves on another, this must be quantified and penalized.

### Phase 5. Hard safety vetoes

Add explicit no-trade vetoes for:
- stale feed
- source disagreement
- extreme jump regime
- extreme sign-flip / chop regime
- insufficient warm-up
- large uncertainty near strike late in the bucket

These should be transparent and inspectable in the UI and logs.

### Phase 6. Post-trade forensic logging

Every trade candidate should log:
- timestamp
- asset
- strike
- current price
- time left
- Kou raw probability
- Kou blend weight
- BS probability
- safety score
- safety reason
- jump / flip / reversal metrics
- final outcome

This is necessary to study failures like:
- `Kou showed 97% at t-1m and the trade still lost`

## Ideas

### Extended Safety Tracker

One strong design idea is to build an extended safety tracker that combines:

- historical regime priors
- very recent live-calculated metrics
- explicit no-go zone logic

The purpose would be to measure whether a trade is actually safe to take right now, even if Kou is showing a high probability.

This would go beyond a simple heuristic score. It would act more like a live safety oracle or veto layer.

Inputs could include:

- current short-horizon volatility
- jump rate
- reversal rate
- sign-flip / chop rate
- trend strength and efficiency
- distance to strike
- time left to expiry
- hour-of-day / hour-of-week historical patterns
- historically learned no-go cells

Possible design:

- one part comes from historical lookup tables or learned priors
- one part comes from the last `30s`, `60s`, `90s`, and `5m` of live microstructure
- the final output is:
  - `safe`
  - `cautious`
  - `no-go`

Why this matters:

- Kou may still be directionally right in theory while the actual live regime is too unstable to trade
- the user explicitly wants protection against late reversals, surprise jumps, and unstable near-strike behavior
- this extended tracker could become the real production safety layer that decides when to abstain

Important principle:

- the safety tracker should not just reuse Kou confidence
- it should be allowed to veto Kou when the live regime looks historically dangerous

### Final Pipeline Calibration

One strong production idea is to do a final end-to-end calibration on the full live decision pipeline, not just on isolated model components.

Meaning:
- run the exact pipeline logic used at decision time
- on real historical data for both assets
- with the same feed assumptions, warm-up logic, Kou blend, safety layer, and veto rules
- then calibrate the final emitted decision confidence against actual outcomes

This matters because production errors can come from the full stack, not just Kou itself:
- feed mismatch
- warm-up blending
- stale data
- safety veto interactions
- late-bucket reversals
- source basis differences

The final calibrated object should ideally answer:
- when the full pipeline says `90%`, how often does it really win?
- when the full pipeline says `safe now`, how often does it actually avoid reversals?
- which asset, hour-of-week, and time-left regimes remain miscalibrated even after the safety layer?

This should be one of the last steps before calling the bot production ready.

## Current Known Limitations

1. The safety layer is still heuristic.
2. The Kou jump model is still heuristically calibrated.
3. The current ETH/XRP probability calibration study is still a `1-minute proxy`, not an exact `10-second` live replay.
4. The old `micro_veto_zones.csv` extraction is too broad and should not be used live as-is.
5. The new late-window policy path is now wired into the live bot, but only as a soft overlay and it is still not fully validated production logic.
6. The browser-fed ETH path is still architecturally weaker than a fully server-side feed.
7. The bot still needs a hard abstain-first production policy.
8. The four available live-capture sessions now have an aggregate report, but the sample is still too small to harden time-of-day or regime-specific thresholds.
9. The rolling `live_policy_disagreements.jsonl` file spans sessions and should not be interpreted as a single-session outcome dataset.
10. Polymarket quote capture is read-only. It records observed CLOB token quotes and visible book size, but it still does not prove personal exchange fills under latency and queue movement.

### 25. Polymarket quote capture and threshold-grid calibration were added

The live pipeline now has a second sidecar:

- [kou_polymarket_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_polymarket_live_capture.py)

It runs beside [kou_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_live_capture.py) and polls the same `kou_dual_compact_web.py` snapshot endpoint.

What it captures:

- current ETH/XRP 5-minute Polymarket market slug and token ids
- YES/NO buy-side best ask and visible ask sizes
- observed YES/NO token `BUY` prices from read-only CLOB endpoints
- paper taker entry price using observed `BUY` price first, falling back to book ask
- paper fillability using visible ask size
- market switch events in `polymarket_markets.jsonl`
- compact quote rows in `polymarket_quotes.jsonl`
- threshold/persistence trigger rows in `polymarket_grid_signals.jsonl`

Important schema decision:

- `polymarket_quotes.jsonl` intentionally stores only a compact Kou reference: symbol, bucket, price/strike, signal, `kou_yes`, `bs_yes`, score label, late policy, and margin.
- `token_prices` is intentionally compact: only `yes.buy_price`, `no.buy_price`, and `buy_price_sum` are stored. `SELL`, midpoint, and last-trade fields were removed because the calibration work only needs the taker buy price for YES/NO.
- `book` is intentionally compact: only `yes.ask`, `yes.ask_size`, `no.ask`, and `no.ask_size` are stored. Bids, bid sizes, spreads, and mids were removed because the current calibration only needs taker entry and visible ask-side fillability.
- `quote_fetch` records the read start/end timestamp and latency so later analysis can see how stale the observed quote may be.
- Quote fetching keeps a usable ask even when the bid side of the book is empty, and it uses one short retry for transient CLOB quote/price failures. This improves fillability capture without materially slowing the live quote loop.
- The sidecar holds the discovered live Polymarket market for the full 5-minute window. It does not rediscover every few seconds while a valid current market is live; it only looks for a new market after the held market has ended, or while no usable market is held.
- Default capture cadence is now `1.0s` normally and `0.5s` in the final `120s`.
- The full model/safety/jump telemetry remains in `snapshots.jsonl`, written by `kou_live_capture.py`.
- `polymarket_grid_signals.jsonl` intentionally does not repeat the full `kou_ref` blob; it stores only trigger context and observed token execution fields.

Default threshold grid:

- thresholds: `0.90,0.91,0.92,0.93,0.94,0.95,0.96`
- hold seconds: `2,3,4,5`
- window: last `90s`

This means every new 4-hour run can evaluate which probability/hold combination would have triggered, at what observed token price, with what visible size, and with what eventual outcome after joining to `bucket_outcomes.jsonl`.

Near-term data plan:

- run several fresh 4-hour sessions, not just one, so the matrix is not overfit to a single time window
- cover different regimes: US regular hours, Europe/pre-US, late US/off-hours, and weekend
- after each batch, rebuild the Polymarket decision matrix and compare win rate, full/partial fill rate, average entry price, ROI, and estimated PnL by `asset x threshold x hold_seconds`

Current handoff state as of 2026-04-23:

- the execution-aware Polymarket sidecar is implemented, smoke-tested, and already validated on a real 4-hour capture
- the strongest completed live session so far is [data/live_capture/20260421T202422Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260421T202422Z)
- that session produced `14978` Kou snapshots, `40234` Polymarket quote rows, `2389` threshold-grid trigger rows, and `98` bucket outcomes
- analyzer outputs for this workflow already exist via [analysis/analyze_polymarket_grid_signals.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/analyze_polymarket_grid_signals.py), and a temporary quick read on the first 4-hour run looked unusually strong, so it should not be overinterpreted yet
- a later mistaken restart session was intentionally deleted and should be ignored; the next agent should not treat it as missing data
- the pipeline now appears stable enough that the bottleneck is dataset breadth, not core implementation
- immediate priority is therefore more clean 4-hour captures across different market regimes, followed by a matrix rebuild and calibration pass

Post-session analyzer:

- [analysis/analyze_polymarket_grid_signals.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/analyze_polymarket_grid_signals.py)

It writes:

- `polymarket_grid_events_enriched.csv`
- `polymarket_grid_matrix.csv`
- `polymarket_grid_matrix_by_timeleft.csv`
- `polymarket_grid_matrix_pivot.csv`
- `analysis_summary.json`

The main matrix groups by `asset x threshold x hold_seconds`. The pivot matrix is the human-readable calibration table: one row per `asset x confidence`, with columns for each hold-second setting. The time-left matrix adds buckets for `60-90s`, `30-60s`, `10-30s`, and `00-10s`. These include trade count, success rate, full/partial/no/unknown fill rates, average observed entry price, average fillable size, average ROI, and total estimated PnL on visible filled size.

Quick live health check:

- [analysis/view_capture_health.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/view_capture_health.py)
- Run this while the bots are active to inspect the current/newest session: `python analysis/view_capture_health.py`
- It reports latest snapshot/quote age, row counts, current ETH/XRP slugs, YES/NO buy prices, ask sizes, quote-fetch latency, recent errors, grid triggers, and outcomes.

Recommended 4-hour capture command set for each test run:

```bash
python kou_dual_compact_web.py
python kou_live_capture.py --max-runtime-seconds 14400
python kou_polymarket_live_capture.py --max-runtime-seconds 14400
python analysis/analyze_polymarket_grid_signals.py --input-root data/live_capture
```

If multiple active sessions are present, pass the same `--session-id` to both capture sidecars.

### 26. Pipeline smoke test was added and passed

A deterministic smoke test now exists:

- [analysis/smoke_test_capture_pipeline.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/smoke_test_capture_pipeline.py)

It starts a mock Kou snapshot server, runs both capture sidecars together, uses `--mock-polymarket` to avoid live network calls, and verifies:

- Kou signals reach `snapshots.jsonl`
- the same signals reach `polymarket_quotes.jsonl`
- observed YES/NO token prices are stored
- grid trigger rows are emitted
- `bucket_outcomes.jsonl` is written
- the grid analyzer can produce a matrix

Smoke result from the first run:

```json
{
  "bucket_outcomes": 2,
  "first_signal": "BUY_YES",
  "first_yes_buy_price": 0.61,
  "grid_signals": 4,
  "ok": true,
  "polymarket_quotes": 10,
  "session_id": "pipeline-smoke",
  "snapshots": 10
}
```

## Suggested Next Coding Task

The next best implementation task is:

Run a fresh 4-hour live capture with both sidecars and then analyze the Polymarket grid matrix. The goal is to decide:

- which threshold/hold pair has the best success rate and estimated ROI
- whether the apparent edge survives observed Polymarket entry prices
- whether fillability collapses in the final `10-30s`, especially when the winning side sells out
- whether XRP needs a separate chop/crossing veto before signal
- whether `CAUTION` and `HARD_NO_GO` should become hard blockers after execution-aware calibration

Older immediate task, now partially completed:

Run the main bot and the sidecar logger in parallel, then compute:
- side-by-side comparisons between the current heuristic safety score and the new late-window policy bands
- which probability bands and persistence rules behave best in live capture
- whether adverse-cross hazard is a better live veto driver than raw reversal rate alone
- whether the current `margin_z` thresholds should be made stricter or looser by asset and time-left
- which jump-threshold sweep behaves best in the captured dataset
- feed / settlement basis diagnostics where possible

Then wire those outputs back into the web dashboard safety layer.

Updated immediate next step from the current state:
- use [data/live_capture/aggregate_analysis/analysis_report.md](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/aggregate_analysis/analysis_report.md) as the current live-capture baseline
- collect more targeted 4-hour sessions across US regular hours, Europe/pre-US, late US/off-hours, and weekend
- after each new batch, rerun [analysis/analyze_polymarket_grid_signals.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/analyze_polymarket_grid_signals.py) and compare the `polymarket_grid_matrix.csv`, `polymarket_grid_matrix_by_timeleft.csv`, and `polymarket_grid_matrix_pivot.csv` outputs
- do not spend more coding time on connection tuning unless a future run shows materially worse quote loss than the already observed baseline; current transient Polymarket errors look acceptable for analysis-grade capture
- the decision target remains a practical calibration matrix over confidence `0.90-0.96` and hold seconds `2-5`, with win rate, fill rate, average entry price, ROI, and estimated PnL by asset and by time-left bucket
- inspect repeated policy overrides in [data/live_policy_disagreements.jsonl](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_policy_disagreements.jsonl)
- investigate the XRP-specific loss pattern where otherwise clear first signals still lose after multiple strike crosses
- decide whether `HARD_NO_GO` should stay a soft cap or become a hard veto after more sessions
- keep the old heuristic only as a fallback until the policy path is validated

## Verification Performed After Recent Changes

The following passed:

```bash
python3 -m py_compile kou_dual_compact_web.py kou_dual_compact_monitor.py analysis/eth_xrp_5m_safety_analyzer.py
python3 -c "import kou_dual_compact_web, kou_dual_compact_monitor; print('ok')"
python3 analysis/eth_xrp_5m_safety_analyzer.py --help
python3 coinbase_download_1m.py --product ETH-USD --months 6 --out ethusd_1m_coinbase_6m.csv
python3 coinbase_download_1m.py --product XRP-USD --months 6 --out xrpusd_1m_coinbase_6m.csv
python3 analysis/eth_xrp_5m_safety_analyzer.py --inputs ethusdt=ethusd_1m_coinbase_6m.csv,xrpusdt=xrpusd_1m_coinbase_6m.csv --output-dir data/analysis_output_5m_safety
# rerun after adding strong-veto outputs
python3 analysis/eth_xrp_5m_safety_analyzer.py --inputs ethusdt=ethusd_1m_coinbase_6m.csv,xrpusdt=xrpusd_1m_coinbase_6m.csv --output-dir data/analysis_output_5m_safety
# rerun after adding first probability-calibration outputs
python3 analysis/eth_xrp_5m_safety_analyzer.py --inputs ethusdt=ethusd_1m_coinbase_6m.csv,xrpusdt=xrpusd_1m_coinbase_6m.csv --output-dir data/analysis_output_5m_safety
python3 -m py_compile binance_download_1s.py coinbase_download_trades_to_1s.py analysis/eth_xrp_5m_microstructure_analyzer.py analysis/plot_5m_microstructure_insights.py
python3 coinbase_download_trades_to_1s.py --product XRP-USD --days 21 --out data/xrpusd_1s_coinbase_21d.csv.gz
python3 coinbase_download_trades_to_1s.py --product ETH-USD --days 21 --out data/ethusd_1s_coinbase_21d.csv.gz
python3 analysis/eth_xrp_5m_microstructure_analyzer.py --inputs ethusdt=data/ethusd_1s_coinbase_21d.csv.gz,xrpusdt=data/xrpusd_1s_coinbase_21d.csv.gz --output-dir data/analysis_output_5m_microstructure
MPLCONFIGDIR=/tmp/matplotlib python3 analysis/plot_5m_microstructure_insights.py --input-dir data/analysis_output_5m_microstructure --output-dir data/analysis_output_5m_microstructure/visuals
# rerun after adding late-window policy and danger-table outputs
python3 analysis/eth_xrp_5m_microstructure_analyzer.py --inputs ethusdt=data/ethusd_1s_coinbase_21d.csv.gz,xrpusdt=data/xrpusd_1s_coinbase_21d.csv.gz --output-dir data/analysis_output_5m_microstructure
MPLCONFIGDIR=/tmp/matplotlib python3 analysis/plot_5m_microstructure_insights.py --input-dir data/analysis_output_5m_microstructure --output-dir data/analysis_output_5m_microstructure/visuals
python3 -m py_compile analysis/build_late_window_safety_policy.py
python3 analysis/build_late_window_safety_policy.py
python3 -m py_compile kou_dual_compact_web.py
# smoke test the disagreement logger path
python3 - <<'PY'
import kou_dual_compact_web as m
obj = m.WebSymbolMonitor.__new__(m.WebSymbolMonitor)
obj.symbol = 'ethusdt'
obj._last_policy_disagreement_key = None
obj._log_policy_disagreement(
    now_ts=1710000000.0,
    state='LIVE',
    time_left_s=60.0,
    current_price=2500.0,
    strike_price=2499.0,
    sample_count=80,
    base_score=82,
    base_label='GOOD',
    base_reason='safe now',
    final_score=24,
    final_label='AVOID',
    final_reason='policy no-go',
    late_policy_level='HARD_NO_GO',
    late_policy_reason='late 60s no-go',
    late_policy_bucket=60,
    margin_z=0.1,
)
PY
python3 -m py_compile analysis/summarize_live_policy_disagreements.py
python3 analysis/summarize_live_policy_disagreements.py --input /tmp/live_policy_disagreements_test.jsonl --output-dir /tmp/live_policy_disagreement_summary_test
python3 -m py_compile kou_live_capture.py
# in-process smoke test of live sidecar capture
python3 - <<'PY'
import kou_live_capture as cap
print('ok', cap.choose_session_id('smoke'))
PY
python3 -m py_compile kou_dual_compact_web.py
# direct Pyth response-shape checks showed both list and dict+parsed Hermes variants
python3 -m py_compile analysis/analyze_live_capture_sessions.py
MPLCONFIGDIR=/tmp/matplotlib python3 analysis/analyze_live_capture_sessions.py --input-root data/live_capture --output-dir data/live_capture/aggregate_analysis
python3 -m py_compile kou_polymarket_live_capture.py analysis/analyze_polymarket_grid_signals.py analysis/smoke_test_capture_pipeline.py tests/test_kou_polymarket_live_capture.py
python3 -m pytest tests/test_kou_polymarket_live_capture.py -q
python3 analysis/smoke_test_capture_pipeline.py
```

## Quick Status Summary

- Kou warm start: `31 candles`
- Kou full readiness: about `61 candles`
- Safety layer: improved, but still not final production oracle
- Jump detection: currently `3.0 sigma` interim heuristic, still not final
- Historical analyzer: scaffold added and run on downloaded ETH/XRP Coinbase data
- Downloaded datasets: ETH Coinbase 6m and XRP Coinbase 6m are now present in MM root
- First historical safety outputs: present in `data/analysis_output_5m_safety`
- No-go candidate table: generated, but too broad to trust as-is
- Strong veto table: generated and much narrower (`50` ETH rows, `42` XRP rows), but not yet validated as production logic
- Probability calibration tables: generated for BS, raw Kou proxy, and blended Kou proxy using a 1-minute historical proxy pipeline
- Current calibration takeaway: Kou proxy is better calibrated at the tails, but BS still has slightly better Brier score and directional hit rate overall
- New sub-minute tooling: added for Binance 1s and Coinbase trades -> 1s aggregation
- New ETH/XRP datasets: Coinbase `1s` history is downloaded for the last `21` days for both assets
- New microstructure outputs: generated from `1s` data and stored in `data/analysis_output_5m_microstructure`
- Current `1s` takeaway: early-bucket near-strike entries are much more dangerous than late-bucket entries on both assets
- Current micro-veto takeaway: the first blanket extraction is too broad and should not be used live as-is
- New late-window policy outputs: generated for the last `90s` using volatility-scaled distance to strike and adverse-cross hazard
- New late-window danger shortlist: much tighter than the blanket micro-veto table (`36` ETH rows and `35` XRP rows)
- Current late-window takeaway: the analysis is now much closer to the real trade window, but still needs `caution` versus `hard no-go` validation
- Draft live policy builder: added and run to create `clear` / `caution` / `hard_no_go` threshold tables
- Current draft policy shape: `hard_no_go` stays concentrated at `margin_z_bin = 0.0`, while `caution` extends up to about `margin_z_bin = 1.0`
- Live bot overlay: the draft late-window policy is now softly wired into the dashboard safety path and exposed as `Late policy`
- Live disagreement tracking: the dashboard now exposes `Heuristic` and `Override`, and meaningful policy overrides are written to `data/live_policy_disagreements.jsonl`
- Live disagreement summary helper: added so the JSONL override log can be aggregated quickly once real data is collected
- Sidecar live capture: added via `kou_live_capture.py` with `5s` capture outside the late window and `1s` capture in the last `120s`
- Richer live telemetry: `safety_components` and jump-threshold sweeps are now exposed in the snapshot payload for later calibration analysis
- ETH source testing: runtime-switchable ETH sources were added, including Coinbase Advanced, Kraken, Gemini, Bitstamp, OKX, and Pyth
- Pyth status: parser was updated after Hermes endpoint checks showed mixed response shapes; source still needs final UI/runtime confirmation from a real bot run
- Default live source choice: both ETH and XRP are now settled on `coinbase-advanced-usd`
- ETH source alignment: historical alignment is still incomplete because downloaded ETH history is Coinbase history, not exact Polymarket/Chainlink settlement history
- Live capture sessions: four sessions are now present under `data/live_capture`; all four have been aggregated in `data/live_capture/aggregate_analysis`
- Aggregate live-capture takeaway: edge is strongly concentrated in the last `90s`, actual first-signal behavior was very strong across all four sessions, near-strike `margin_z` remains the main safety risk, and observed first-signal losses were XRP buckets with repeated strike crosses
- Polymarket quote capture: added as a read-only second sidecar with observed YES/NO token prices, visible book size, paper fillability, and threshold-grid trigger logging
- Grid analyzer: added to produce threshold x hold-second matrices for success rate, fill rate, observed entry price, ROI, and estimated PnL
- Pipeline smoke test: added and passed with both sidecars running against the same mock snapshot stream
- Next data-analysis task: run a fresh 4-hour session with both sidecars, then analyze `polymarket_grid_matrix.csv` to validate time-of-day differences, XRP chop/crossing vetoes, fill decay near close, and any promotion of `CAUTION` or `HARD_NO_GO` toward hard blocking
- Production readiness: not done yet

## Current Handoff - 2026-04-29

This section supersedes the older "next data-analysis task" notes above.

### Where the project stands now

The live-capture evidence base has grown materially:

- session folders under `data/live_capture`: `16`
- usable complete buckets in the aggregate analyzer: `1251`
- joined live snapshot rows: `193433`
- first-signal rows: `1063`
- Polymarket grid rows: `20357`
- Polymarket grid loss rows: `298`
- unique Polymarket grid loss clusters by `session_id x asset x bucket_end x side`: `24`

One capture should be excluded from model conclusions:

- `20260425T092927Z` stayed in `BOOT` for the full run
- it has Polymarket quotes, but no usable price/strike/model/signal data
- do not treat it as evidence for signal quality

Current first-signal aggregate:

- all first signals: `1044/1063`, `98.2%`
- ETH first signals: `525/532`, `98.7%`
- XRP first signals: `519/531`, `97.7%`

Current Polymarket-grid aggregate:

- previous Polymarket-aware data: `10995/11129`, `98.8%`
- newer Polymarket-aware data: `9064/9228`, `98.2%`
- total Polymarket-aware data: `20059/20357`, about `98.5%`

Main interpretation:

- the late first-signal layer remains robust
- BS is still the better broad snapshot probability model by Brier/accuracy
- Kou remains useful as a selective late signal, not as a globally calibrated probability engine
- XRP remains the cleanest paper-trading candidate
- ETH has improved enough to continue paper evaluation, but not enough for live sizing
- losses are clustered by bucket, so row-level Polymarket matrix results overstate independence

### Autoresearch scaffold now exists

The offline autoresearch lab is in:

- [analysis/autoresearch_kou/program.md](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/program.md)
- [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py)
- [analysis/autoresearch_kou/evaluate_candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/evaluate_candidate.py)
- [analysis/autoresearch_kou/candidates/cluster_guard_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/cluster_guard_v1.py)
- [tests/test_autoresearch_kou.py](/Users/viktorzettel/Downloads/ViktorAI/MM/tests/test_autoresearch_kou.py)

The evaluator now reports three dataset views:

- `first_signals`: the original first-signal quality view
- `polymarket_grid`: row-level threshold/hold execution matrix
- `polymarket_clusters`: one count per `session_id x asset x bucket_end x side`

The cluster-level view is now the main guardrail against overfitting.

Baseline cluster result from the current data:

```text
polymarket_clusters
train:      475/489, 97.14%
validation: 118/125, 94.40%
test:       174/177, 98.31%
```

Exploratory `cluster_guard_v1` result:

```text
polymarket_clusters
train:      468/477, 98.11%
validation: 113/119, 94.96%
test:       169/171, 98.83%
```

Interpretation of `cluster_guard_v1`:

- useful probe
- not production-ready
- catches some losses, but blocks winners too
- validation economics are not strong enough to promote it

### What the user should do next

Run more clean, caffeinated 4-hour captures with both sidecars, prioritizing targeted coverage:

1. `2` more US regular-hours sessions
2. `1-2` Europe/pre-US or after-hours sessions as comparison/control
3. avoid relying on any session that stays in `BOOT` or has no grid triggers

After every new capture batch, rerun:

```bash
python3 analysis/analyze_live_capture_sessions.py \
  --input-root data/live_capture \
  --output-dir data/live_capture/forensic_analysis/live_aggregate

python3 analysis/analyze_polymarket_grid_signals.py \
  --input-root data/live_capture \
  --output-dir data/live_capture/forensic_analysis/polymarket_grid

python3 analysis/compile_live_capture_forensic_report.py

python3 analysis/autoresearch_kou/evaluate_candidate.py
python3 analysis/autoresearch_kou/evaluate_candidate.py \
  --candidate analysis/autoresearch_kou/candidates/cluster_guard_v1.py
```

### What the next agent should implement

Do not jump to live-money execution.

The next coding/research task is:

1. add more candidate veto files under `analysis/autoresearch_kou/candidates/`
2. optimize against `polymarket_clusters`, not only `polymarket_grid`
3. compare baseline vs candidates by validation/test cluster metrics
4. only keep candidates that improve cluster losses without excessive winner blocking
5. add a per-candidate summary helper if comparisons become tedious

Promising candidate families:

- XRP-only high-confidence band, roughly `0.95-0.96` threshold with `2-4s` hold
- higher-fill XRP band, roughly `0.90-0.92` threshold with `3-4s` hold
- ETH paper-only band around `0.94/2s` and `0.96/4s`
- flat-settlement protection, because some grid losses settle `flat`
- cluster-level vetoes using `time_left_s`, `threshold`, `hold_seconds`, `policy_margin_z`, `path_30s_cross_count`, and `path_30s_adverse_share`

Current operating recommendation:

- continue read-only capture and offline analysis
- paper/shadow execution is the next practical milestone
- no real money until a cluster-aware candidate survives fresh future sessions that were not used to design it

## Current Handoff Update - 2026-04-29 Reversal-Regime Audit

This section supersedes the older 2026-04-29 handoff numbers above after the latest captured session and the new reversal-regime analysis.

### Updated evidence base

Current aggregate after adding [data/live_capture/20260429T060822Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260429T060822Z):

- session folders under `data/live_capture`: `17`
- complete buckets in the aggregate analyzer: `1346`
- joined live snapshot rows: `208129`
- first-signal rows: `1146`
- first-signal result: `1122/1146`, about `97.91%`
- Polymarket grid rows: `22729`
- Polymarket cluster count by `session_id x asset x bucket_end x side`: `885`
- Polymarket cluster losses: `31`

Latest session [data/live_capture/20260429T060822Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260429T060822Z):

- duration: full `4h`
- first signals: `78/83`, about `93.98%`
- Polymarket grid rows: `2372`
- Polymarket row losses: `117`
- independent loss clusters: about `7`

Interpretation:

- the newest session was a useful stress session
- it reduced the aggregate first-signal rate slightly, but did not break the edge
- the main risk remains clustered reversal/settlement traps, not weak average signal quality
- row-level grid stats still overstate independence; use cluster metrics for candidate decisions

### Option paper interpretation

The local paper [option_paper.pdf](/Users/viktorzettel/Downloads/ViktorAI/MM/option_paper.pdf) supports the idea that Kou can outperform Black-Scholes for crypto option pricing. The paper calibrates Kou to option surfaces across strikes and maturities and reports much lower pricing errors than Black-Scholes, especially for BTC and also materially for ETH.

Important production caveat:

- the paper is an option-surface calibration study
- this bot is a live `5m` binary/digital market decision system
- our current Kou parameters are estimated from short-window realized spot movement, not from a risk-neutral option surface
- digital payoff probabilities near expiry are extremely sensitive to volatility, jumps, feed alignment, and settlement timing

Working conclusion:

- Kou is still theoretically appropriate for crypto because it models asymmetric jumps
- the current live implementation should not be assumed to be correctly calibrated just because the paper favors Kou
- the present data says Kou is useful as a selective late signal, but the live probability scale and veto stack still need calibration

Likely calibration weaknesses to investigate next:

- fixed `3.0 sigma` jump threshold is still heuristic
- short rolling windows can misestimate sparse jump intensity and jump-tail parameters
- physical realized-return calibration is not the same as risk-neutral market-price calibration
- broad snapshot Brier score can favor BS even when Kou is better in the late high-confidence subset
- exact feed/settlement alignment still matters more for `5m` digitals than for vanilla option-surface fitting

### Reversal-regime audit added

A new cluster-level audit script now exists:

- [analysis/analyze_reversal_regime_vetoes.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/analyze_reversal_regime_vetoes.py)

Default command:

```bash
python3 analysis/analyze_reversal_regime_vetoes.py
```

Outputs:

- [data/live_capture/forensic_analysis/reversal_regimes/reversal_regime_report.md](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/reversal_regimes/reversal_regime_report.md)
- [data/live_capture/forensic_analysis/reversal_regimes/cluster_events.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/reversal_regimes/cluster_events.csv)
- [data/live_capture/forensic_analysis/reversal_regimes/loss_clusters.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/reversal_regimes/loss_clusters.csv)
- [data/live_capture/forensic_analysis/reversal_regimes/feature_risk_matrix.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/reversal_regimes/feature_risk_matrix.csv)
- [data/live_capture/forensic_analysis/reversal_regimes/cluster_regime_matrix.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/reversal_regimes/cluster_regime_matrix.csv)
- [data/live_capture/forensic_analysis/reversal_regimes/simple_veto_scan.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/reversal_regimes/simple_veto_scan.csv)
- [data/live_capture/forensic_analysis/reversal_regimes/recommended_veto_candidates.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/reversal_regimes/recommended_veto_candidates.csv)

Current reversal-regime findings:

- baseline cluster result: `854/885`, about `96.50%`
- loss clusters with prior 60s crossing: `16/31`
- loss clusters with prior 60s adverse share above `35%`: `8/31`
- loss clusters with `policy_margin_z < 2.0`: `14/31`
- highest simple-risk cells are near-strike and very-late clusters, especially `00-10s` and `margin_z < 1.5`

Interpretation:

- yes, reversing regimes are partly observable before entry
- a hard skip layer can remove many losses
- a minority of losses still look clean before failure, so the goal is loss reduction, not loss elimination
- skipping half the markets is a valid research direction if the objective is capital preservation

### New autoresearch candidate

New candidate:

- [analysis/autoresearch_kou/candidates/regime_skip_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/regime_skip_v1.py)
- [analysis/autoresearch_kou/candidates/regime_skip_entry_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/regime_skip_entry_v1.py)
- [analysis/autoresearch_kou/candidates/regime_entry_curve_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/regime_entry_curve_v1.py)

Candidate rule:

- leave `first_signals` unchanged
- for Polymarket grid entries, allow only when:
  - prior `60s` adverse share is `<= 5%`
  - prior `60s` margin-z improvement is `>= 1.0`
  - at least `5s` remain before expiry

Evaluation command:

```bash
python3 analysis/autoresearch_kou/evaluate_candidate.py \
  --candidate analysis/autoresearch_kou/candidates/regime_skip_v1.py
```

Latest run:

- [analysis/autoresearch_kou/runs/20260429T140515Z_regime_skip_v1.json](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/runs/20260429T140515Z_regime_skip_v1.json)

Cluster comparison against current baseline:

```text
baseline polymarket_clusters
validation: 118/125, 94.40%, losses 7
test:       261/271, 96.31%, losses 10

regime_skip_v1 polymarket_clusters
validation: 90/92, 97.83%, losses 2
test:       192/196, 97.96%, losses 4
```

Interpretation of `regime_skip_v1`:

- promising as a capital-preservation candidate
- avoids many clustered losses
- blocks many winners
- validation `ci_low_minus_avg_entry` is still negative because remaining entries can be expensive
- not production-ready and not a live-money rule

Cost-aware follow-up `regime_skip_entry_v1`:

- same prior-60s cleanliness rule as `regime_skip_v1`
- also skips observed entry prices above `0.96`
- latest run: [analysis/autoresearch_kou/runs/20260429T141222Z_regime_skip_entry_v1.json](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/runs/20260429T141222Z_regime_skip_entry_v1.json)

Cluster result:

```text
regime_skip_entry_v1 polymarket_clusters
validation: 45/47, 95.74%, paper ROI 12.74%
test:       70/74, 94.59%, paper ROI 9.24%
```

Interpretation of `regime_skip_entry_v1`:

- improves paper ROI and conservative entry spread
- allows far fewer clusters
- win-rate confidence becomes weaker because the remaining sample is small and cheaper contracts are not always safer
- useful evidence, but the simple `96c` cap is too blunt for production

Balanced asset/threshold curve `regime_entry_curve_v1`:

- same prior-60s cleanliness rule as `regime_skip_v1`
- entry caps:
  - ETH: `0.98` for thresholds `0.90-0.93`, `0.99` for thresholds `0.94-0.96`
  - XRP: `0.98` for thresholds `0.90-0.93`, no practical cap for thresholds `0.94-0.96`
- latest run: [analysis/autoresearch_kou/runs/20260429T142313Z_regime_entry_curve_v1.json](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/runs/20260429T142313Z_regime_entry_curve_v1.json)

Cluster comparison:

```text
baseline
validation: 118/125, losses 7, paper ROI 4.29%
test:       261/271, losses 10, paper ROI 5.49%

regime_skip_v1
validation: 90/92, losses 2, paper ROI 3.00%
test:       192/196, losses 4, paper ROI 5.05%

regime_skip_entry_v1
validation: 45/47, losses 2, paper ROI 12.74%
test:       70/74, losses 4, paper ROI 9.24%

regime_entry_curve_v1
validation: 87/89, losses 2, paper ROI 3.72%
test:       186/190, losses 4, paper ROI 4.48%
```

Interpretation of `regime_entry_curve_v1`:

- confirms that asset/threshold-specific price discipline can be added without collapsing the sample as much as the global `96c` cap
- does not clearly beat `regime_skip_v1` on test economics
- slightly improves validation ROI versus `regime_skip_v1`, but test ROI is lower
- the next curve should be learned from train only and should consider side, threshold, and visible fill size, not just asset and threshold

### Updated next steps

Do this next:

1. keep `regime_skip_v1` as an exploratory reference candidate, not production logic
2. use `regime_skip_entry_v1` as proof that price discipline matters, but do not promote the fixed `96c` cap
3. keep `regime_entry_curve_v1` as the balanced reference candidate, not as production logic
4. add a train-only curve builder so future entry caps are generated reproducibly rather than hand picked
5. add a Kou calibration audit focused on late high-confidence decisions, not broad all-snapshot Brier alone
6. run `2-3` future clean captures only as out-of-sample validation after candidate changes
7. do not spend real money until a candidate survives future sessions that were not used to design it

## Current Handoff Update - 2026-04-29 Train-Only Entry Curve Builder

The hand-picked entry curves above have now been converted into a reproducible train-only builder.

New builder:

- [analysis/autoresearch_kou/build_entry_curve_candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/build_entry_curve_candidate.py)

Default command:

```bash
python3 analysis/autoresearch_kou/build_entry_curve_candidate.py
```

Profile-specific generated candidates:

- [analysis/autoresearch_kou/candidates/generated_entry_curve_safety_first_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/generated_entry_curve_safety_first_v1.py)
- [analysis/autoresearch_kou/candidates/generated_entry_curve_balanced_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/generated_entry_curve_balanced_v1.py)
- [analysis/autoresearch_kou/candidates/generated_entry_curve_roi_seek_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/generated_entry_curve_roi_seek_v1.py)

Builder reports:

- [analysis/autoresearch_kou/generated_entry_curves/entry_curve_builder_report_safety_first.md](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/generated_entry_curves/entry_curve_builder_report_safety_first.md)
- [analysis/autoresearch_kou/generated_entry_curves/entry_curve_builder_report_balanced.md](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/generated_entry_curves/entry_curve_builder_report_balanced.md)
- [analysis/autoresearch_kou/generated_entry_curves/entry_curve_builder_report_roi_seek.md](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/generated_entry_curves/entry_curve_builder_report_roi_seek.md)

What the builder does:

- uses the same chronological train/validation/test split as the locked evaluator
- learns caps from train sessions only
- keeps the prior-60s regime rule:
  - `path_60s_adverse_share <= 0.05`
  - `path_60s_margin_z_change >= 1.0`
  - `time_left_s >= 5`
- learns caps by `asset x side x threshold`
- evaluates three risk profiles:
  - `safety_first`: preserve sample and avoid tiny lucky cheap-entry cells
  - `balanced`: mix ROI and conservative edge
  - `roi_seek`: chase more ROI while accepting more sample loss

Locked evaluator comparison:

```text
baseline
validation: 118/125, losses 7, ROI 4.29%, ci-entry -1.18pp
test:       261/271, losses 10, ROI 5.49%, ci-entry 1.83pp

regime_skip_v1
validation: 90/92, losses 2, ROI 3.00%, ci-entry -3.53pp
test:       192/196, losses 4, ROI 5.05%, ci-entry 1.14pp

generated_entry_curve_safety_first_v1
validation: 90/92, losses 2, ROI 3.37%, ci-entry -2.65pp
test:       189/193, losses 4, ROI 5.08%, ci-entry 1.06pp

generated_entry_curve_balanced_v1
validation: 89/91, losses 2, ROI 3.39%, ci-entry -2.71pp
test:       188/192, losses 4, ROI 5.09%, ci-entry 1.04pp

generated_entry_curve_roi_seek_v1
validation: 87/89, losses 2, ROI 3.48%, ci-entry -2.84pp
test:       181/185, losses 4, ROI 5.42%, ci-entry 0.99pp
```

Important interpretation:

- all generated profiles preserve the main win: test cluster losses fall from `10` to `4`
- the ROI-seeking profile gives slightly higher test ROI but blocks more clusters
- the safety-first profile is currently the best reference candidate because it preserves the most sample while retaining the loss reduction
- none of the generated profiles is ready for real-money execution because validation `ci_low_minus_avg_entry` is still negative

Decision from the current evidence:

- use `generated_entry_curve_safety_first_v1` as the next shadow/paper reference candidate
- keep `generated_entry_curve_roi_seek_v1` as a research branch only
- do not optimize further on the current test sessions; the next improvement must be validated on future captures

Best next step for profitability and safety:

1. freeze `generated_entry_curve_safety_first_v1` as the current candidate
2. run `2-3` future clean 4h captures without changing the candidate
3. after those captures, rebuild aggregate/grid/reversal reports and rerun the locked evaluator
4. promote only if future test sessions keep cluster losses low without destroying ROI
5. then build a shadow execution logger that records hypothetical orders using this candidate, still without spending real money

## Current Handoff Update - 2026-04-29 Lean Validation Capture

The capture sidecars now have an explicit lean validation mode for the next out-of-sample sessions.

Updated files:

- [kou_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_live_capture.py)
- [kou_polymarket_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_polymarket_live_capture.py)

New flag:

```bash
--validation-profile
```

Meaning for [kou_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_live_capture.py):

- capture profile in metadata: `candidate_validation`
- fine window: `120s`
- fine cadence: `1.0s`
- coarse cadence: `5.0s`

Meaning for [kou_polymarket_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_polymarket_live_capture.py):

- capture profile in metadata: `candidate_validation`
- thresholds: `0.90,0.91,0.92,0.93,0.94,0.95,0.96`
- hold seconds: `2` only
- grid window: `90s`
- fine window: `120s`
- fine cadence: `0.5s`
- coarse cadence: `2.0s`

Why this is the right mode now:

- current candidate validation needs the threshold curve, observed entry price, side, prior 60s adverse share, prior 60s margin-z change, and bucket outcome
- it does not need all `2,3,4,5s` hold variants anymore
- keeping only `2s` persistence reduces grid rows while preserving the current candidate validation surface
- this is still read-only and paper-only; it does not place orders

Recommended 4h command for the next sessions:

```bash
SESSION_ID=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "data/live_capture/$SESSION_ID"

caffeinate -dimsu -t 14700 &
CAFFEINATE_PID=$!

python kou_dual_compact_web.py > "data/live_capture/$SESSION_ID/web.log" 2>&1 &
WEB_PID=$!

sleep 5

python kou_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --max-runtime-seconds 14400 \
  > "data/live_capture/$SESSION_ID/kou_live_capture.log" 2>&1 &
KOU_PID=$!

python kou_polymarket_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --max-runtime-seconds 14400 \
  > "data/live_capture/$SESSION_ID/polymarket_capture.log" 2>&1 &
PM_PID=$!

echo "Started lean validation 4h capture session: $SESSION_ID"
echo "Caffeinate PID: $CAFFEINATE_PID"
echo "Web PID: $WEB_PID | Kou capture PID: $KOU_PID | Polymarket capture PID: $PM_PID"
echo ""
echo "Check after 20-30 seconds with:"
echo "python analysis/view_capture_health.py"
```

Verification after adding the profile:

```bash
python3 -m py_compile kou_live_capture.py kou_polymarket_live_capture.py
python3 -m pytest tests/test_kou_polymarket_live_capture.py -q
python3 analysis/smoke_test_capture_pipeline.py --runtime-seconds 4.5
```

All passed at the time this note was written.

## Current Handoff Update - 2026-04-29 XRP-First Candidate

Newest conclusion after the 3h US-hours lean validation session `20260429T151512Z`:

- XRP is currently the cleaner production candidate than ETH.
- ETH still produces strong-looking signals, but too many of them require expensive `0.98-0.99` entries. One ETH loss at that price erases many small wins.
- XRP has better execution-aware evidence: lower average entry, stronger confidence-vs-entry margin, and cleaner newest-session behavior.

Implemented candidate files:

- [analysis/autoresearch_kou/candidates/xrp_only_safety_first_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_safety_first_v1.py)
- [analysis/autoresearch_kou/candidates/xrp_only_cap98_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_v1.py)
- [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py) now points to `xrp_only_cap98_v1` as the active research candidate.

The active candidate does this:

- blocks ETH completely
- only allows XRP
- keeps the prior-60s clean-path filter:
  - `time_left_s >= 5`
  - `path_60s_adverse_share <= 0.05`
  - `path_60s_margin_z_change >= 1.0`
- keeps the train-only XRP entry curve
- adds a global XRP max entry cap: `entry_price <= 0.98`

Locked evaluator result for `xrp_only_cap98_v1`:

```text
polymarket_clusters train:      121/122, ROI 9.22%, ci-entry +5.27pp
polymarket_clusters validation: 48/48,   ROI 14.04%, ci-entry +11.19pp
polymarket_clusters test:       60/62,   ROI 7.14%, ci-entry +1.95pp
```

Newest session-only result for `20260429T151512Z`:

```text
XRP cap98 candidate clusters: 19/19, avg entry 0.9032, paper ROI 10.72%
```

Interpretation:

- This is the best current research candidate.
- It is still paper-only. The `0.98` cap was selected after seeing current data, so it must be validated forward on fresh sessions before any real-money use.
- ETH should remain in observation/research only until an ETH-specific entry cap or veto survives future validation.

Recommended next capture mode:

```bash
python kou_polymarket_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --assets xrp \
  --max-runtime-seconds 14400
```

For full terminal runs, keep [kou_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_live_capture.py) running as before, but pass `--assets xrp` to [kou_polymarket_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_polymarket_live_capture.py). More ETH capture is optional research; XRP-only Polymarket capture is now the highest-signal next validation path.

## Current Handoff Update - 2026-04-30 Live Shadow Execution Logger

Newest operating position:

- Active research candidate: [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py), currently pointing to `xrp_only_cap98_required_context_v2`.
- Trading scope: XRP only. ETH remains observation/research only.
- Latest 5h forward session `20260430T110859Z` supported the XRP direction:
  - completed XRP 5-minute markets: `61`
  - candidate trades: `19`
  - candidate wins: `19/19`
  - skipped markets: `42/61`, or about `68.9%`
  - average candidate entry: about `0.9337`
  - paper cluster ROI: about `7.1%`

Important interpretation:

- This is encouraging, but still not enough for real-money deployment.
- The current candidate is intentionally selective. It is trying to avoid the reversal/trap regimes by skipping most markets.
- More raw data captures help less than before. The next useful evidence is execution-shaped data: exact candidate order time, quoted entry, visible size, and settled PnL.

### Live shadow execution logger

Implemented in [kou_polymarket_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_polymarket_live_capture.py).

New flag:

```bash
--shadow-candidate analysis/autoresearch_kou/candidate.py
```

What it does:

- imports the active candidate module and calls `score_grid_event(row)` for each grid trigger
- writes the first executable candidate-approved paper order per `symbol x 5m bucket`
- never submits, signs, posts, or cancels a real order
- records the exact candidate decision, side, entry price, price source, visible ask size, hypothetical fill size, and cost
- reads `bucket_outcomes.jsonl` from the same session and writes settlement rows when outcomes are available

New output files when the flag is enabled:

- `shadow_orders.jsonl`
- `shadow_order_settlements.jsonl`

Schema intent:

- `shadow_orders.jsonl` is the paper order-intent ledger
- `shadow_order_settlements.jsonl` is the paper PnL ledger
- all rows include `mode: read_only_shadow_no_order` and `real_order_submitted: false`
- de-duplication is one shadow order per `symbol x bucket`, so multiple threshold variants do not become multiple paper trades

Recommended next 4h XRP shadow validation command:

```bash
SESSION_ID=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "data/live_capture/$SESSION_ID"

caffeinate -dimsu -t 14700 &
CAFFEINATE_PID=$!

python kou_dual_compact_web.py > "data/live_capture/$SESSION_ID/web.log" 2>&1 &
WEB_PID=$!

sleep 5

python kou_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --max-runtime-seconds 14400 \
  > "data/live_capture/$SESSION_ID/kou_live_capture.log" 2>&1 &
KOU_PID=$!

python kou_polymarket_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --assets xrp \
  --shadow-candidate analysis/autoresearch_kou/candidate.py \
  --max-runtime-seconds 14400 \
  > "data/live_capture/$SESSION_ID/polymarket_capture.log" 2>&1 &
PM_PID=$!

echo "Started XRP shadow validation 4h capture session: $SESSION_ID"
echo "Caffeinate PID: $CAFFEINATE_PID"
echo "Web PID: $WEB_PID | Kou capture PID: $KOU_PID | Polymarket capture PID: $PM_PID"
echo ""
echo "Check after 20-30 seconds with:"
echo "python analysis/view_capture_health.py"
```

Recommended next analysis task:

1. Run one fresh XRP-only shadow validation session with the command above.
2. Compare `shadow_order_settlements.jsonl` against the older replay ledger in `data/live_capture/forensic_analysis/shadow_replay`.
3. Promote only if the shadow ledger still shows no material losses, entries stay below the cap, and visible liquidity is enough for the intended tiny test size.
4. Do not add a hard time-of-day veto yet. Keep reporting US regular, US after-hours, Europe/pre-US, and late-US windows, then only hard-block a time window if the shadow ledger shows repeated execution-aware weakness there.

Verification after implementation:

```bash
python3 -m py_compile kou_polymarket_live_capture.py analysis/view_capture_health.py tests/test_kou_polymarket_live_capture.py
python3 -m pytest tests/test_kou_polymarket_live_capture.py -q
python3 analysis/smoke_test_capture_pipeline.py --runtime-seconds 4.5
```

All passed when this note was written.

## Current Handoff Update - 2026-04-30 Historical Shadow Replay

The old captures were replayed with the same logic as the live shadow logger.

New script:

- [analysis/replay_shadow_execution.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/replay_shadow_execution.py)

Command:

```bash
python3 analysis/replay_shadow_execution.py
```

Replay outputs:

- [shadow_replay_report.md](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay/shadow_replay_report.md)
- [shadow_replay_orders.jsonl](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay/shadow_replay_orders.jsonl)
- [shadow_replay_settlements.jsonl](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay/shadow_replay_settlements.jsonl)
- [shadow_replay_settlements.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay/shadow_replay_settlements.csv)
- [shadow_replay_session_summary.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay/shadow_replay_session_summary.csv)
- [shadow_replay_time_window_summary.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay/shadow_replay_time_window_summary.csv)

Important implementation finding:

- The previous active candidate `xrp_only_cap98_v1` treated missing clean-path fields as safe.
- That is too permissive for production.
- A stricter candidate was added:
  - [xrp_only_cap98_required_context_v2.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2.py)
- [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py) now points to this stricter v2 candidate.

Historical shadow replay with the stricter v2 candidate:

```text
sessions scanned:        17
completed XRP markets:   617
shadow orders settled:   199
wins / losses:           197 / 2
win rate:                98.99%
average entry:           0.8817
skip rate:               67.75%
paper ROI, 5-share size: 12.28%
```

By UTC time window:

```text
Europe/pre-US: 77/79, 2 losses, ROI 14.52%
Late US/Asia:   5/5,  0 losses, ROI 25.94%
US after-hours: 39/39, 0 losses, ROI 16.07%
US regular:     76/76, 0 losses, ROI 7.59%
```

The two remaining historical losses were both in the same Europe/pre-US session, `20260429T060822Z`.

Interpretation:

- The historical replay supports XRP-only and supports the strict context requirement.
- It does not prove production safety because much of this data helped discover the rule.
- The latest truly useful test is now one fresh capture using the new live shadow logger and the stricter active candidate.
- Do not hard-block Europe/pre-US yet. The two losses are worth watching, but that window is still net positive in the replay.

Verification after replay and candidate v2:

```bash
python3 -m py_compile analysis/autoresearch_kou/candidate.py analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2.py analysis/replay_shadow_execution.py
python3 -c 'import kou_polymarket_live_capture as c; m=c.load_shadow_candidate("analysis/autoresearch_kou/candidate.py"); print(m["name"])'
python3 analysis/replay_shadow_execution.py
python3 analysis/autoresearch_kou/evaluate_candidate.py --candidate analysis/autoresearch_kou/candidate.py --no-log
```

The candidate import printed `xrp_only_cap98_required_context_v2`.

## Current Handoff Update - 2026-04-30 Pre-Registered Ultra-Safe Challenger

Do not change the active live shadow candidate before the next 6h capture.

Current active candidate remains:

- [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py)
- active policy: `xrp_only_cap98_required_context_v2`

Reason:

- the current candidate is already strict and now requires complete live clean-path context
- changing the active rule immediately before the fresh 6h session would make the next capture harder to compare with the existing replay
- the right move is to collect the next shadow ledger under the current active candidate, then replay/evaluate the stricter challenger on the same fresh data

Pre-registered ultra-safe challenger for comparison:

```text
asset == XRP
entry_price <= 0.98
time_left_s >= 5
path_60s_adverse_share <= 0.05
path_60s_margin_z_change >= 1.25
safety_score >= 87
```

Why this challenger exists:

- in the historical shadow replay, the two remaining v2 losses had only barely-passing 60s margin improvement and lower safety scores
- adding `path_60s_margin_z_change >= 1.25` or `safety_score >= 87` removed those replay losses individually
- combining both was stricter but still left a usable historical sample:

```text
current v2 replay:                         199 trades, 197/199 wins, ~67.7% market skip
path_60s_margin_z_change >= 1.25:          154 trades, 154/154 wins, ~75.0% market skip
safety_score >= 87:                        153 trades, 153/153 wins, ~75.2% market skip
path_60s_margin_z_change >= 1.25 AND
  safety_score >= 87:                      125 trades, 125/125 wins, ~79.7% market skip
```

Important overfitting warning:

- only two remaining losses were used to motivate this stricter rule
- that is not enough to prove a universal reversal-trap pattern
- the ultra-safe challenger should be treated as pre-registered for the next analysis, not promoted yet

Next analysis after the fresh 6h capture:

1. analyze the live `shadow_orders.jsonl` and `shadow_order_settlements.jsonl` from the current active candidate
2. replay the ultra-safe challenger on the same fresh session data
3. compare current v2 vs ultra-safe on:
   - trades kept
   - wins/losses
   - average entry
   - paper ROI
   - skip rate
   - visible liquidity
4. only switch the active candidate if the stricter challenger improves safety without reducing the sample to statistical noise

## Current Handoff Update - 2026-05-01 Fresh 6h Shadow Session Result

Fresh 6h shadow validation session:

- session id: `20260430T214102Z`
- runtime: `2026-04-30T21:41:07Z` to `2026-05-01T03:41:08Z`
- capture mode: XRP-only Polymarket sidecar, validation profile, live shadow logger enabled
- active shadow candidate during capture: `xrp_only_cap98_required_context_v2`

Raw session health:

```text
snapshots:            22,466
polymarket quotes:    23,761
grid triggers:        470
bucket outcomes:      146
shadow orders:        36
shadow settlements:   36
completed XRP markets: 72
```

The session stopped normally at the 6h runtime limit. The health checker may show `WARN` after completion only because the latest rows are old; that is expected for a stopped session.

### Active v2 Candidate Result

Current active candidate, `xrp_only_cap98_required_context_v2`:

```text
shadow trades:        36
wins / losses:        35 / 1
win rate:             97.22%
Wilson low:           ~85.83%
average entry:        0.8761
paper cost, 5-share:  157.70
paper PnL, 5-share:   +17.30
paper ROI:            10.97%
XRP market skip rate: 50.00%
```

Interpretation:

- the live shadow execution chain worked end to end
- the active candidate remained profitable on this fresh out-of-sample session
- but the active candidate did catch one real reversal/trap loss

The losing active-v2 shadow trade:

```text
captured_at:      2026-05-01T00:29:34Z
side:             YES
entry:            0.66
time_left_s:      24.8
threshold:        0.90
safety_score:     75
path_60s_z_change: 1.135
settled_side:     NO
paper PnL:        -3.30 at 5-share size
```

This loss matters because it had the same general weakness that motivated the pre-registered ultra-safe challenger: low safety score and only barely-passing 60s margin improvement.

### Ultra-Safe Challenger Check

The pre-registered ultra-safe challenger was not active during capture, but it can be evaluated as a strict subset of the captured shadow/order features.

Ultra-safe rule:

```text
asset == XRP
entry_price <= 0.98
time_left_s >= 5
path_60s_adverse_share <= 0.05
path_60s_margin_z_change >= 1.25
safety_score >= 87
```

Fresh 6h result for this challenger:

```text
shadow trades kept:       23 / 36
wins / losses:            23 / 0
win rate:                 100.00%
Wilson low:               ~85.69%
average entry:            0.8626
paper cost, 5-share:      99.20
paper PnL, 5-share:       +15.80
paper ROI:                15.93%
XRP market skip rate:     68.06%
```

Interpretation:

- the ultra-safe challenger skipped the one fresh losing trade
- it also skipped 12 winning trades, so it is materially more selective
- despite fewer trades, it kept most of the session PnL and improved ROI
- this is the first genuinely useful forward evidence in favor of promoting the ultra-safe rule

### Updated Aggregate Replay

After adding this fresh 6h session, the historical/live replay for the current active v2 candidate is:

```text
sessions scanned:        18
completed XRP markets:   689
shadow orders settled:   235
wins / losses:           232 / 3
win rate:                98.72%
Wilson low:              ~96.31%
average entry:           0.8808
skip rate:               65.89%
paper ROI, 5-share size: 12.08%
```

By UTC time window:

```text
Europe/pre-US: 77/79, 2 losses, ROI 14.52%
Late US/Asia:  29/30, 1 loss,  ROI 14.81%
US after-hours: 50/50, 0 losses, ROI 14.03%
US regular:     76/76, 0 losses, ROI 7.59%
```

Updated replay report:

- [shadow_replay_report.md](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay/shadow_replay_report.md)

### Recommendation

This session is positive but changes the recommendation:

- do not go live with money yet
- the current v2 candidate is profitable, but it allowed one fresh avoidable loss
- the pre-registered ultra-safe challenger passed its first forward check
- the next engineering step should be to implement the ultra-safe challenger as a separate candidate file and run one more shadow capture with it active

Decision status:

```text
ETH: observation only
XRP current v2: profitable shadow baseline, not live-money ready
XRP ultra-safe: preferred next active shadow candidate, pending one more fresh validation
```

Implementation note:

- inactive candidate file added for the challenger:
  - [xrp_only_ultra_safe_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1.py)
- [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py) was not switched in this update; it still points to `xrp_only_cap98_required_context_v2`
- locked evaluator run written to:
  - [20260501T074750Z_xrp_only_ultra_safe_v1.json](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/runs/20260501T074750Z_xrp_only_ultra_safe_v1.json)

Locked evaluator result for `xrp_only_ultra_safe_v1`:

```text
polymarket_clusters train:      83/83, 0 losses, ROI 10.64%
polymarket_clusters validation: 47/48, 1 loss,  ROI 9.21%
polymarket_clusters test:       33/33, 0 losses, ROI 8.00%
```

This reinforces the same conclusion: the ultra-safe rule is promising, but still needs one active forward shadow session before promotion.

## Current Handoff Update - 2026-05-01 Trading Hours Interpretation

Question reviewed:

- Are some trading hours clearly better or worse?
- Should the bot block US hours because US regular trading is known to be more volatile?

Current answer:

- Do not hard-block US regular hours yet.
- US regular hours are likely more volatile, but in the current shadow replay they are not worse by outcome.
- The US regular problem is mostly price/ROI: entries are more expensive, so each correct trade pays less.
- The observed losses are better explained by low safety score and barely-passing 60s margin improvement than by a simple time-of-day rule.

Current active-v2 aggregate by UTC time window:

```text
UTC window        Berlin time       Result        Avg entry   ROI        Read
Europe/pre-US     08:00-15:30       77/79         0.851       14.52%     good, 2 losses
US regular        15:30-22:00       76/76         0.929        7.59%     clean but expensive
US after-hours    22:00-02:00       50/50         0.877       14.03%     very good so far
Late US/Asia      02:00-08:00       29/30         0.842       14.81%     good, 1 loss
```

Simple visual read:

```text
Europe/pre-US   08-15:30  ███████████████░  77/79  good but not perfect
US regular      15:30-22  ████████████████  76/76  cleanest by wins, lower ROI
US after-hours  22-02     ████████████████  50/50  strong
Late US/Asia    02-08     ███████████████░  29/30  good but small sample
```

Hour-level picture:

```text
Loss clusters so far:
- Berlin 08:00 hour: 8/9, negative ROI in replay
- Berlin 09:00 hour: 7/8, still positive ROI
- Berlin 02:00 hour: 5/6, still positive ROI

Strong hours with many samples:
- Berlin 11:00 hour: 22/22, high ROI
- Berlin 00:00 hour: 19/19, high ROI
- Berlin 17:00 hour: 20/20, lower ROI because entries are expensive
```

Interpretation:

- The data does not support a blanket "block US regular hours" rule.
- US regular hours are clean in current outcome count but pay less because entry prices are high.
- The weak hours are not yet supported by enough samples to become hard time vetoes.
- If a time filter is added later, it should be a soft report/monitor first, not a hard production block.

Preferred safety approach right now:

```text
Use stricter regime filters across all hours:
- safety_score >= 87
- path_60s_margin_z_change >= 1.25

Do not use a hard time-of-day block yet.
```

Reason:

- the ultra-safe rule caught the fresh 6h loss directly
- a time block would be cruder and could remove many good trades for the wrong reason
- if 1-2 more active ultra-safe shadow sessions show repeated weakness in a specific hour band, then add a time veto candidate and evaluate it separately

## Current State For Future Agents - 2026-05-01

If you are a future agent reading this file, this is the current state:

```text
ETH: observation only; do not trade ETH.
XRP: only asset under consideration.
Live money: not ready yet.
Active candidate file: analysis/autoresearch_kou/candidate.py
Active candidate policy: xrp_only_cap98_required_context_v2
Preferred next candidate: xrp_only_ultra_safe_v1
Next recommended action: switch active candidate to ultra-safe only if the user agrees, then run one more 6h shadow session.
```

Current candidate files:

- Active baseline:
  - [xrp_only_cap98_required_context_v2.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2.py)
- Inactive challenger:
  - [xrp_only_ultra_safe_v1.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1.py)
- Pointer:
  - [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py)

Important caution:

- Several older sections of this roadmap mention earlier candidates such as `xrp_only_cap98_v1`.
- Those are historical context only.
- The current baseline is `xrp_only_cap98_required_context_v2`.
- The preferred next challenger is `xrp_only_ultra_safe_v1`.

Most recent decisive evidence:

```text
Fresh 6h session 20260430T214102Z:
current v2:   35/36 wins, 1 loss, ROI 10.97%
ultra-safe:   23/23 wins, 0 losses, ROI 15.93%
```

Why ultra-safe is preferred next:

- it was pre-registered before checking the fresh 6h session
- it skipped the fresh v2 losing trade
- it improved ROI while reducing trade count
- it matches the observed weakness pattern: low safety score and barely-passing 60s margin improvement

Why ultra-safe is not promoted to live money yet:

- one fresh forward session is useful but not enough
- the locked evaluator still shows one validation cluster loss for ultra-safe
- the next step should be another active shadow session with ultra-safe, not live order execution

Recommended next terminal command after switching active candidate to ultra-safe:

```bash
SESSION_ID=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "data/live_capture/$SESSION_ID"

caffeinate -dimsu -t 21900 &
CAFFEINATE_PID=$!

python kou_dual_compact_web.py > "data/live_capture/$SESSION_ID/web.log" 2>&1 &
WEB_PID=$!

sleep 5

python kou_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --max-runtime-seconds 21600 \
  > "data/live_capture/$SESSION_ID/kou_live_capture.log" 2>&1 &
KOU_PID=$!

python kou_polymarket_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --assets xrp \
  --shadow-candidate analysis/autoresearch_kou/candidate.py \
  --max-runtime-seconds 21600 \
  > "data/live_capture/$SESSION_ID/polymarket_capture.log" 2>&1 &
PM_PID=$!

echo "Started XRP ultra-safe shadow validation 6h capture session: $SESSION_ID"
echo "Caffeinate PID: $CAFFEINATE_PID"
echo "Web PID: $WEB_PID | Kou capture PID: $KOU_PID | Polymarket capture PID: $PM_PID"
echo ""
echo "Check after 20-30 seconds with:"
echo "python analysis/view_capture_health.py"
```

Before giving that command to the user, make sure [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py) actually points to `xrp_only_ultra_safe_v1.py`. At the time this note was written, it still points to `xrp_only_cap98_required_context_v2.py`.

After the next session:

1. Run `python analysis/view_capture_health.py --session-id <SESSION_ID>` for a quick integrity check.
2. Analyze `shadow_orders.jsonl` and `shadow_order_settlements.jsonl`.
3. Run `python3 analysis/replay_shadow_execution.py` to refresh aggregate replay.
4. Compare active ultra-safe vs current-v2 baseline on:
   - wins/losses
   - number of trades
   - skip rate
   - average entry
   - paper ROI
   - visible liquidity
   - time-window distribution
5. Only discuss tiny live-money testing if the active ultra-safe session is clean or has at most one clearly explainable, non-structural loss.

## Current Handoff Update - 2026-05-01 XRP Source-Basis Audit and v3 Near-Strike Veto

Question reviewed:

- Coinbase Advanced and Polymarket/Chainlink XRP are very close most of the time.
- The dangerous case is not normal source drift; it is the final seconds when price is only a few ticks from the strike.
- In those razor-thin states, the Kou probability can shoot up mechanically near expiry, while a tiny source/rounding difference can still flip the real settlement side.

One-hour source-alignment capture:

- capture file: [xrp_alignment_20260501T145929Z.csv](/Users/viktorzettel/Downloads/ViktorAI/MM/data/source_alignment/xrp_alignment_20260501T145929Z.csv)
- summary file: [xrp_alignment_20260501T145929Z_summary.json](/Users/viktorzettel/Downloads/ViktorAI/MM/data/source_alignment/xrp_alignment_20260501T145929Z_summary.json)
- method: Coinbase Advanced XRP/USD from Python websocket, Polymarket/Chainlink XRP from browser-assisted RTDS helper, sampled at `1s`.
- completed rows: `3591`
- rows with both feeds populated: `3583`
- fresh paired rows: `3540`

Observed source-basis result:

```text
Raw abs live basis, coinbase - poly, all fresh rows:
mean   0.000111
median 0.000084
p90    0.000216
p95    0.000287
p99    0.000582
max    0.003377  transient mid-bucket spike

Raw abs live basis, final 30s:
mean   0.000108
median 0.000075
p95    0.000306
max    0.000533

Raw abs live basis, final 10s:
mean   0.000122
median 0.000087
p95    0.000322
max    0.000533
```

Observed strike-basis result:

```text
abs strike basis:
mean   0.000141
median 0.000120
p90    0.000183
p95    0.000336
max    0.000336
```

Four-decimal display read:

- Polymarket displays XRP to four decimals, so small raw differences can look larger or smaller depending on rounding.
- Same 4dp displayed price occurred only about `31.2%` of all fresh rows, `28.5%` in the final `30s`, and `20.9%` in the final `10s`.
- Most visible differences were still small, usually `0.0001-0.0003`.
- User typo note: the intended veto discussion was `0.0003`, not `0.003`.

Important observed close-disagreement example:

```text
close tick: 2026-05-01T15:44:58Z
time left: about 0.206s

Polymarket/Chainlink:
price  1.39856340
strike 1.39856123
diff  +0.00000217  => barely YES

Coinbase Advanced:
price  1.39850000
strike 1.39870000
diff  -0.00020000  => NO
```

Caveat:

- This was inferred from the last fresh observed tick, not from an official settlement record.
- It is still enough to prove the risk class exists: if the asset is only a few ten-thousandths from strike, source alignment and display rounding can decide the side.

Recommended v3 ultra-safe challenger:

```text
xrp_only_ultra_safe_near_strike_v3

Base rule:
- asset must be XRP
- entry price <= 0.98
- time_left_s >= 5
- path_60s_adverse_share <= 0.05
- path_60s_margin_z_change >= 1.25
- safety_score >= 87

New hard source/expiry veto:
- if time_left_s <= 30 and abs(price - strike) < 0.0004: skip
- if time_left_s <= 10 and abs(price - strike) < 0.0006: skip
```

Why these numbers:

- `0.0003` is a reasonable minimum because it roughly matches the observed final-window p95 basis.
- `0.0004 / 0.0006` is the safer first v3 audit proposal because it leaves room for brief source spikes, strike rounding, tick timing, and final-second exponential probability artifacts.
- If trade count collapses too much in replay, compare a lighter variant using `0.0003 / 0.0005`.

Relationship to historical losses:

- The new near-strike veto is important, but it does not appear to be the main explanation for the previous losses.
- Active v2 historical shadow replay showed `232/235` wins and `3` losses.
- Those three active-v2 losses were all late-window trades:

```text
2026-04-29T06:44:45Z  YES lost, time_left 13.6s, side_prob 0.9464, entry 0.96
2026-04-29T07:59:44Z  NO lost,  time_left 14.6s, side_prob 0.9454, entry 0.85
2026-05-01T00:29:34Z  YES lost, time_left 24.8s, side_prob 0.9202, entry 0.66
```

Broader Polymarket cluster-loss pattern:

```text
known loss clusters/events: 32
losses in 10-30s:          18
losses in 00-10s:           9
losses in 30-60s:           4
losses in 60-90s:           1

contextual losses reviewed: 25
median time_left:           13.2s
median side_probability:    0.928
median safety_score:        88
median policy_margin_z:     1.843

losses with path_60s_margin_z_change < 1.25: 18/25
losses with safety_score < 87:               11/25
losses failing either ultra-safe criterion:  19/25
losses with time_left <= 30s:                21/25
```

Interpretation:

- The dominant historical loss pattern is late-window reversal/chop: weak 60s margin improvement, adverse path, repeated strike-cross behavior, or merely OK safety.
- Many historical losses were not literally only `0.0001-0.0003` from strike; several had larger `margin_z`.
- So v3 should be understood as a separate tail-risk guard, not as the reason all prior losses happened.
- The ultra-safe layer handles the larger historical pattern; the new hard near-strike veto handles the newly observed source/rounding/expiry cliff.

Recommended next step:

1. Do not edit the active live/shadow candidate silently.
2. Create `xrp_only_ultra_safe_near_strike_v3.py` as a separate challenger.
3. Replay v3 against the same historical and forward shadow datasets.
4. Compare v2, ultra-safe v1, and v3 on wins/losses, trade count, skipped winners, average entry, and ROI.
5. Only promote v3 if it blocks razor-thin close-risk without destroying too many good late-window trades.

## Current Handoff Update - 2026-05-02 Near-Strike Replay Matrix

The replay plan was implemented without switching the active candidate pointer.

Files added:

- [xrp_only_cap98_required_context_v2_near_strike_light.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2_near_strike_light.py)
- [xrp_only_cap98_required_context_v2_near_strike_conservative.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2_near_strike_conservative.py)
- [xrp_only_ultra_safe_v1_near_strike_light.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_light.py)
- [xrp_only_ultra_safe_v1_near_strike_conservative.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_conservative.py)

Replay support changes:

- [kou_polymarket_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_polymarket_live_capture.py) now carries `price`, `strike`, and `delta_bps` into future grid events and candidate rows.
- [analysis/replay_shadow_execution.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/replay_shadow_execution.py) enriches old grid events from each session's `snapshots.jsonl`, so historical hard-veto replays use actual captured `price` and `strike` instead of a rough proxy.
- Active pointer [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py) was intentionally left unchanged and still points to `xrp_only_cap98_required_context_v2`.

Hard-veto variants tested:

```text
light:
  time_left <= 30s and abs(price - strike) < 0.0003 => skip
  time_left <= 10s and abs(price - strike) < 0.0005 => skip

conservative:
  time_left <= 30s and abs(price - strike) < 0.0004 => skip
  time_left <= 10s and abs(price - strike) < 0.0006 => skip
```

Replay output root:

- [shadow_replay_variants](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/shadow_replay_variants)

Replay matrix:

```text
Candidate                  Trades   Wins/Losses   Win rate   Avg entry   ROI       Vs parent
v2                         235      232/3         98.72%     0.8808      12.08%    baseline
v2 + light veto            210      207/3         98.57%     0.8920      10.51%    blocked 25 winners, 0 losses
v2 + conservative veto     189      187/2         98.94%     0.8975      10.24%    blocked 45 winners, 1 loss
ultra-safe                 191      190/1         99.48%     0.8771      13.42%    baseline
ultra-safe + light veto    175      174/1         99.43%     0.8802      12.97%    blocked 16 winners, 0 losses
ultra-safe + conservative  155      155/0         100.00%    0.8910      12.24%    blocked 35 winners, 1 loss
```

Answers from the replay:

- The hard veto only helped when using the conservative thresholds.
- The light veto did not remove any loss in this replay; it only removed winners.
- `v2 + conservative` still had two losses and lower ROI than plain ultra-safe, so it is not the preferred path.
- Plain ultra-safe is still the best balanced safety/ROI rule: `190/191`, `13.42%` replay ROI.
- Ultra-safe plus conservative veto is the best safety-first rule: `155/155`, `12.24%` replay ROI.

What happened to the three active-v2 replay losses:

```text
2026-04-29T06:44:45Z  YES lost, dist 0.000600, safety 84, zchg60 1.109
  blocked by ultra-safe; not blocked by conservative near-strike because it was exactly at the 10s-window threshold.

2026-04-29T07:59:44Z  NO lost, dist 0.000330, safety 86, zchg60 1.244
  blocked by conservative near-strike; not blocked by light near-strike.

2026-05-01T00:29:34Z  YES lost, dist 0.000460, safety 75, zchg60 1.135
  blocked by ultra-safe; not blocked by conservative near-strike because the 30s-window threshold is 0.0004.
```

Interpretation:

- The main historical loss pattern is still better handled by ultra-safe: safety score and 60s margin improvement were the larger weakness.
- The new hard near-strike veto catches a real tail-risk class, but it is not free. The conservative veto removed one loss and 35 ultra-safe winners.
- For live-money readiness, the correct next shadow candidate is therefore the safety-first version:

```text
analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_conservative.py
```

This is not promoted to live money yet. It should be run as the next active forward shadow candidate.

Source decision:

- Still pending.
- Coinbase Advanced remains the repo default unless the runtime source override is used.
- Polymarket/Chainlink is now the preferred source to forward-test for XRP, based on the user's visual audit so far.
- The direct Python `poly-chainlink` source can fetch initial RTDS snapshot rows, but the latest 60s probe did not receive continuous live 1s updates.
- Therefore `browser-poly-chainlink` remains the validated live-update source for the next serious shadow capture.
- The helper page for tomorrow's visual check is [xrp_live_price_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/xrp_live_price_web.py).

Recommended next live-shadow command after the source decision:

```bash
SESSION_ID=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "data/live_capture/$SESSION_ID"

caffeinate -dimsu -t 21900 &
CAFFEINATE_PID=$!

python kou_dual_compact_web.py \
  --symbols xrpusdt \
  --display-source-overrides xrpusdt=browser-poly-chainlink \
  --model-source-overrides xrpusdt=browser-poly-chainlink \
  > "data/live_capture/$SESSION_ID/web.log" 2>&1 &
WEB_PID=$!

sleep 5

python kou_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --max-runtime-seconds 21600 \
  > "data/live_capture/$SESSION_ID/kou_live_capture.log" 2>&1 &
KOU_PID=$!

python kou_polymarket_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --assets xrp \
  --shadow-candidate analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py \
  --max-runtime-seconds 21600 \
  > "data/live_capture/$SESSION_ID/polymarket_capture.log" 2>&1 &
PM_PID=$!

echo "Started XRP browser Poly/Chainlink exec-guard shadow validation session: $SESSION_ID"
echo "Caffeinate PID: $CAFFEINATE_PID"
echo "Web PID: $WEB_PID | Kou capture PID: $KOU_PID | Polymarket capture PID: $PM_PID"
```

For later no-browser testing only, the direct source command is:

```bash
python kou_dual_compact_web.py \
  --symbols xrpusdt \
  --display-source-overrides xrpusdt=poly-chainlink \
  --model-source-overrides xrpusdt=poly-chainlink \
  > "data/live_capture/$SESSION_ID/web.log" 2>&1 &
```

Do not use this for the next serious shadow session yet. The missing link is continuous live updates: direct `poly-chainlink` currently receives fresh initial snapshots but then stalls.

## 2026-05-03 Direct Source and Execution Guard Update

Implemented:

- `kou_dual_compact_web.py` now accepts `poly-chainlink` / `polymarket-chainlink` as direct Python Polymarket RTDS source aliases.
- The direct stream handles Polymarket's initial snapshot payload and compatible `crypto_prices` / `crypto_prices_chainlink` RTDS message shapes.
- Important correction after the 60s probe: direct `poly-chainlink` gets initial snapshot rows but does not yet get continuous live 1s updates.
- 60s direct-source age probe: `60` samples, `0` missing seconds, `1` unique price, mean age `30.957s`, p95 `57.486s`, max `60.490s`.
- Raw socket diagnostic showed initial rows with fresh age around `1-2s`, followed by websocket timeouts instead of steady live updates.
- `kou_polymarket_live_capture.py` now carries source age, source names, requested size, visible book ask, and endpoint/book delta into grid candidate rows.
- New candidate: [xrp_only_ultra_safe_v1_near_strike_exec_guard.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py).
- The dry-run sniper foundation now uses visible `book_ask_price` as executable entry and rejects missing book, thin book, stale source, and large endpoint/book disagreement.

Replay result for the new execution-realism candidate:

```text
Fresh 20260502T204924Z: 5 trades, 5/0, avg executable entry 0.5320, ROI 87.97%
Aggregate 19 sessions:  40 trades, 40/0, avg executable entry 0.9125, ROI 9.59%
```

Interpretation:

- The exec guard is intentionally much stricter than ultra-safe + near-strike conservative.
- It blocks many theoretical winners, but it addresses the production problem discovered in the latest audit: endpoint/display prices can be non-executable when the visible book is missing, thin, sold out, or much higher.
- The user's observation that a clear winning token can display as `0ct` while the losing token shows `1ct` reinforces this rule: final production execution should trust the book, not the UI display or `/price` endpoint alone.
- Next proof needed: one fresh `browser-poly-chainlink` shadow session with the exec-guard candidate to see whether trade count is still acceptable.
- Separate source-engineering task: fix the missing direct RTDS live-update link, or explicitly run the final source through a supervised headless browser adapter.

## 2026-05-04 Browser Source Session and Stability Patch

Fresh forward session:

```text
session:              20260504T130507Z
runtime:              about 2h40m
source:               browser-poly-chainlink for display and model
candidate:            xrp_only_ultra_safe_v1_near_strike_exec_guard
completed markets:    32
grid trigger rows:    215
shadow orders:        2
settlements:          2
wins / losses:        2 / 0
paper cost:           9.55
paper PnL:            +0.45
paper ROI:            +4.71%
```

Takeaway:

- The exec-guard candidate behaved as intended: it accepted only clean, executable, full-size book situations.
- The two accepted trades had fresh source age (`0.6s`, `0.2s`) and settled correctly.
- Low trade count is acceptable if win quality remains high, especially during higher-volatility US hours.
- Source stability is still the main engineering issue: median source age was good (`0.4s`), but stale periods remained (`p95 58.8s`, max `208.6s`).
- The exec guard blocked stale rows from becoming orders, but the source should still be improved before live-money thinking.

Patch after the session:

- The browser RTDS bridge previously ignored Polymarket batch payloads under `payload.data`; it only forwarded `payload.value`.
- This likely caused stale periods even when the browser websocket received snapshot batches.
- [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py) now forwards the newest row from `payload.data`.
- Browser watchdog was relaxed from `1.2s` to `6s`, reconnect delay from `150ms` to `500ms`, and stale close threshold from `1s` to `3s`.
- Next session should measure whether stale periods above `3s` shrink.

## Future Real-Money Bot Note - Polymarket CLOB V2 / Geoblock

As of the April 28, 2026 Polymarket exchange upgrade, any future real-money
execution layer must be treated as a new integration task, not a small patch on
top of the current read-only/shadow stack.

Important implications:

- The current Kou dashboard, source probe, live capture, Polymarket quote
  capture, and shadow execution paths are read-only / paper-only and remain
  useful for research.
- Legacy CLOB V1 SDKs and V1-signed orders are no longer production-compatible.
- A real-money bot must use CLOB V2 order signing or the V2 SDK.
- Production remains `https://clob.polymarket.com`, but the signing payload,
  exchange contracts, fee model, and collateral assumptions changed.
- Collateral is now pUSD, not USDC.e; an API-only trading wallet must handle
  the pUSD / approval path correctly before order placement.
- Order signing changed: EIP-712 exchange domain version is now `2`, the signed
  order uses `timestamp`, `metadata`, and `builder`, and no longer signs
  `nonce`, `feeRateBps`, or `taker`.
- Fees are determined at match time, so execution simulations and live order
  sizing should not assume the old embedded-fee model.
- Before building any live execution path, verify geoblock eligibility with
  `https://polymarket.com/api/geoblock` from the actual intended operating
  environment, and do not design the bot to bypass Polymarket geographic
  restrictions.

Sources checked on 2026-05-01:

- Polymarket CLOB V2 migration docs
- Polymarket April 28, 2026 exchange-upgrade help article
- Polymarket geographic-restrictions docs

## 2026-05-08 VPS Tiny-Live Readiness Update

Current VPS status:

- AWS Ireland VPS path is validated with `browser-poly-chainlink`.
- Latest 4h VPS dry run completed cleanly with fresh source age, low quote latency, no stale-source periods above `10s`, and one v3 exec-guard signal.
- The same session replayed under plain v2 would have produced more buys, but many were near-strike/late-window trades that v3 intentionally blocks.
- v3 remains the preferred first live-money candidate because the first live test should validate execution safety, not maximize trade count.

Execution status:

- [polymarket_token_sniper.py](/Users/viktorzettel/Downloads/ViktorAI/MM/polymarket_token_sniper.py) now has a CLOB V2 market-order submit path behind explicit live arming.
- [deployment/vps/run_4h_live_tiny.sh](/Users/viktorzettel/Downloads/ViktorAI/MM/deployment/vps/run_4h_live_tiny.sh) starts the supervised tiny live session.
- The live launcher refuses to run unless `KOU_I_UNDERSTAND_REAL_MONEY=YES` is set.
- Hard caps remain `1 pUSD/order`, `4 pUSD/session`, `4 orders/session`, `FOK` only.
- [deployment/vps/env_fingerprint.py](/Users/viktorzettel/Downloads/ViktorAI/MM/deployment/vps/env_fingerprint.py) compares local and VPS `.env` values using fingerprints only, without printing secrets.

Data capture during live:

- Live mode still writes the full research dataset: quotes, markets, grid signals, shadow orders, sniper signals, sniper plans, live results, live ledger, and logs.
- The first tiny live session should be analyzed as both an execution test and a new forward dataset for v3 review / possible v4 safety design.
- It is reasonable to wait for a supervised evening window instead of starting during the US open; the operator should watch the first submit and stop on any ambiguous result.

## 2026-05-08 First Real-Money Execution Result

The first supervised tiny live execution test succeeded.

Session:

- [data/live_capture/20260508T182926Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260508T182926Z)
- Source: `browser-poly-chainlink`
- Candidate: `xrp_only_ultra_safe_v1_near_strike_exec_guard`
- Signal: `NO`
- Market: `xrp-updown-5m-1778269200`
- Entry/book ask: `0.98`
- Submitted cost: `1.47 pUSD`
- Requested size: `1.5`
- Order type: `FOK`
- CLOB response: `success=true`, `status=matched`
- Plan-to-submit delay: about `0.612s`

Captured expiry outcome:

- Closest expiry sample: `2026-05-08T19:44:59.001Z`
- Price: `1.4201`
- Strike: `1.4207`
- Outcome: `NO`
- The real `NO` order appears to have been correct from the captured Polymarket/Chainlink stream.

Operational findings:

- Wallet auth, pUSD execution, CLOB V2 market-order submit, token resolution, and ledger recording all worked on the VPS.
- The earlier `$1` attempt safely found a CLOB minimum-size issue: a marketable BUY amount of `$0.98` is rejected because CLOB requires at least `$1`.
- [polymarket_token_sniper.py](/Users/viktorzettel/Downloads/ViktorAI/MM/polymarket_token_sniper.py) now treats live market BUY `amount` as pUSD and floors to the minimum where required.
- This was an execution-path milestone, not proof that unattended production trading is ready.
