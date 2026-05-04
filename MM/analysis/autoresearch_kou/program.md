# Kou Autoresearch Program

This directory is an offline research lab inspired by Karpathy's autoresearch
pattern: keep the evaluator fixed, edit one candidate file, run repeated short
experiments, and log every result.

## Rule Zero

During a research run, edit only:

- `analysis/autoresearch_kou/candidate.py`

Do not edit the evaluator, generated reports, or historical capture data to
make a candidate look better. If the evaluator needs to change, start a new
research phase and treat old results as not directly comparable.

## Objective

Find changes that improve out-of-sample behavior on captured ETH/XRP 5-minute
binary-market data without overfitting session noise.

Primary targets:

1. Reduce trap losses through candidate vetoes.
2. Improve final probability calibration through post-processing of `kou_yes`
   and `bs_yes`.
3. Preserve trade count and do not buy performance by abstaining from almost
   everything.

Later targets:

1. Tune deeper Kou internals such as jump threshold, calibration window, and
   blend curve.
2. Shadow-test promising candidates during live sessions before any live money.

## Candidate API

`candidate.py` must define:

```python
CANDIDATE_NAME = "short_name"
CANDIDATE_DESCRIPTION = "What this candidate changes."

def score_first_signal(row):
    return {
        "allow_trade": True,
        "adjusted_prob_yes": row["kou_yes"],
        "reason": "short_reason",
    }

def score_grid_event(row):
    return {
        "allow_trade": True,
        "reason": "short_reason",
    }
```

The evaluator keeps the original captured side. A candidate may veto a row or
recalibrate the YES probability, but it should not pretend it can rewrite what
the live bot would have seen.

## Evaluation Command

Refresh the normal analysis artifacts first when new sessions are available:

```zsh
python3 analysis/analyze_live_capture_sessions.py \
  --input-root data/live_capture \
  --output-dir data/live_capture/forensic_analysis/live_aggregate

python3 analysis/analyze_polymarket_grid_signals.py \
  --input-root data/live_capture \
  --output-dir data/live_capture/forensic_analysis/polymarket_grid

python3 analysis/compile_live_capture_forensic_report.py
```

Then evaluate the candidate:

```zsh
python3 analysis/autoresearch_kou/evaluate_candidate.py
```

Fast smoke run:

```zsh
python3 analysis/autoresearch_kou/evaluate_candidate.py --max-rows 500 --no-log
```

Outputs:

- JSON reports in `analysis/autoresearch_kou/runs/`
- TSV experiment log at `analysis/autoresearch_kou/results.tsv`

Reversal-regime audit after new capture batches:

```zsh
python3 analysis/analyze_reversal_regime_vetoes.py
```

This writes cluster-level loss/regime outputs to
`data/live_capture/forensic_analysis/reversal_regimes/`. Use this before
designing hard skip candidates, because Polymarket grid rows are highly
correlated inside each market bucket.

## Metrics To Care About

For `first_signals`:

- validation/test win rate
- Wilson lower bound
- YES Brier score
- side-probability Brier score
- losses avoided versus winners blocked

For `polymarket_grid`:

- validation/test win rate
- Wilson lower bound
- `ci_low_minus_avg_entry`
- paper ROI
- losses avoided versus winners blocked
- ETH/XRP subgroup behavior

For `polymarket_clusters`:

- validation/test win rate at one count per `session x asset x bucket x side`
- cluster losses avoided versus winner clusters blocked
- representative first-allowed entry/ROI
- ETH/XRP subgroup behavior without threshold-row duplication

## Overfitting Guardrails

- Trust validation/test before train.
- Prefer candidates that improve multiple sessions, not one cluster.
- Treat grid rows as correlated; use `polymarket_clusters` to check whether
  gains survive when one bucket counts once.
- Penalize excessive abstention.
- Be suspicious of rules that key on exact `session_id`, market slug, or one
  timestamp.
- Promote a candidate only if it survives a future captured session that was
  not present when it was designed.

## First Research Brief

The first serious experiment should target:

> Can we reduce ETH/US-hours trap losses while preserving XRP's stronger edge?

Promising features already captured:

- `time_left_s`
- `threshold`
- `hold_seconds`
- `asset` / `symbol`
- `policy_margin_z` or `margin_z`
- `path_30s_cross_count`
- `path_30s_adverse_share`
- `path_30s_margin_z_change`
- `safety_label`
- `policy_level`

## Current Candidate Notes - 2026-04-29

`analysis/autoresearch_kou/candidates/regime_skip_v1.py` is the first strict
reversal-regime skip candidate. It leaves first signals unchanged and vetoes
Polymarket grid entries unless the prior 60s path is clean:

- `path_60s_adverse_share <= 0.05`
- `path_60s_margin_z_change >= 1.0`
- `time_left_s >= 5`

Latest cluster comparison on the current captured dataset:

```text
baseline polymarket_clusters
validation: 118/125, 94.40%, losses 7
test:       261/271, 96.31%, losses 10

regime_skip_v1 polymarket_clusters
validation: 90/92, 97.83%, losses 2
test:       192/196, 97.96%, losses 4
```

Interpretation:

- this is a useful capital-preservation probe
- it avoids clustered losses, but blocks many winner clusters
- it is not production-ready because validation economics remain fragile
- the next candidate should add entry-price sensitivity and probably separate
  ETH from XRP

`analysis/autoresearch_kou/candidates/regime_skip_entry_v1.py` adds a blunt
entry-price cap to the same prior-60s cleanliness rule:

- skip observed entries above `0.96`

Latest cluster result:

```text
regime_skip_entry_v1 polymarket_clusters
validation: 45/47, 95.74%, paper ROI 12.74%
test:       70/74, 94.59%, paper ROI 9.24%
```

Interpretation:

- price discipline improves paper ROI and conservative entry spread
- the sample becomes much smaller
- the win-rate confidence gets weaker
- the next entry-aware candidate should use asset/threshold-specific price
  curves rather than one global `96c` cap

`analysis/autoresearch_kou/candidates/regime_entry_curve_v1.py` is the first
balanced asset/threshold-specific curve:

- ETH cap: `0.98` for thresholds `0.90-0.93`, `0.99` for `0.94-0.96`
- XRP cap: `0.98` for thresholds `0.90-0.93`, no practical cap for `0.94-0.96`

Latest cluster result:

```text
regime_entry_curve_v1 polymarket_clusters
validation: 87/89, 97.75%, paper ROI 3.72%
test:       186/190, 97.89%, paper ROI 4.48%
```

Interpretation:

- this is a more balanced version than the global `96c` cap
- it keeps most of the loss reduction from `regime_skip_v1`
- it does not clearly improve test economics versus `regime_skip_v1`
- the next research step should be a train-only curve builder that outputs
  reproducible candidate cap tables, ideally including side and fillability

## Train-Only Entry Curve Builder - 2026-04-29

The reproducible builder now exists:

```zsh
python3 analysis/autoresearch_kou/build_entry_curve_candidate.py
```

It writes:

- `analysis/autoresearch_kou/candidates/generated_entry_curve_safety_first_v1.py`
- `analysis/autoresearch_kou/generated_entry_curves/entry_curve_builder_report_safety_first.md`

Optional profile branches:

```zsh
python3 analysis/autoresearch_kou/build_entry_curve_candidate.py \
  --profile balanced \
  --candidate-out analysis/autoresearch_kou/candidates/generated_entry_curve_balanced_v1.py

python3 analysis/autoresearch_kou/build_entry_curve_candidate.py \
  --profile roi_seek \
  --candidate-out analysis/autoresearch_kou/candidates/generated_entry_curve_roi_seek_v1.py
```

The builder learns caps from train sessions only and then emits a static
candidate module. It uses caps by `asset x side x threshold`, while preserving
the same prior-60s cleanliness filter:

- `path_60s_adverse_share <= 0.05`
- `path_60s_margin_z_change >= 1.0`
- `time_left_s >= 5`

Locked evaluator comparison:

```text
baseline
validation: 118/125, losses 7, ROI 4.29%
test:       261/271, losses 10, ROI 5.49%

generated_entry_curve_safety_first_v1
validation: 90/92, losses 2, ROI 3.37%
test:       189/193, losses 4, ROI 5.08%

generated_entry_curve_balanced_v1
validation: 89/91, losses 2, ROI 3.39%
test:       188/192, losses 4, ROI 5.09%

generated_entry_curve_roi_seek_v1
validation: 87/89, losses 2, ROI 3.48%
test:       181/185, losses 4, ROI 5.42%
```

Current research decision:

- use `generated_entry_curve_safety_first_v1` as the reference shadow/paper candidate
- keep `roi_seek` as a research branch only, because it gets a little more ROI by shrinking the sample
- do not tune further against the current test sessions
- the next legitimate proof must come from future captures that were not used in this design loop

## XRP-First Candidate - 2026-04-29

After adding the lean US-hours validation session `20260429T151512Z`, XRP is
the cleaner execution-aware path than ETH:

- newest session XRP candidate clusters were perfect under the stricter cap
- ETH still shows high-probability traps at expensive `0.98-0.99` entries
- XRP has stronger confidence-vs-entry evidence in the Polymarket matrix

Implemented candidates:

- `analysis/autoresearch_kou/candidates/xrp_only_safety_first_v1.py`
- `analysis/autoresearch_kou/candidates/xrp_only_cap98_v1.py`

`analysis/autoresearch_kou/candidate.py` now points to `xrp_only_cap98_v1` as
the active default candidate for the locked evaluator.

Active rule:

- block ETH
- allow only XRP
- keep the clean-path regime filter:
  - `time_left_s >= 5`
  - `path_60s_adverse_share <= 0.05`
  - `path_60s_margin_z_change >= 1.0`
- keep the train-only XRP entry curve
- add global `entry_price <= 0.98`

Locked evaluator result:

```text
xrp_only_cap98_v1 polymarket_clusters
train:      121/122, ROI 9.22%, ci-entry +5.27pp
validation: 48/48,   ROI 14.04%, ci-entry +11.19pp
test:       60/62,   ROI 7.14%, ci-entry +1.95pp
```

Newest session-only result:

```text
20260429T151512Z XRP cap98 clusters: 19/19, avg entry 0.9032, paper ROI 10.72%
```

Important caveat:

- the `0.98` global cap was selected after looking at current data
- treat this as the next forward-validation candidate, not as production proof
- collect fresh XRP-only Polymarket validation sessions before any money use
