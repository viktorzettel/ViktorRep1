# Shadow Replay Report

Candidate: `xrp_only_ultra_safe_v1_near_strike_exec_guard`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py`
Sessions scanned: `19`

## Headline

- Completed XRP markets in scanned sessions: `761`
- Shadow orders: `40`
- Known settled orders: `40`
- Wins/losses: `40/0`
- Win rate: `100.00%`
- Average entry: `0.9125`
- Replay skip rate across completed XRP markets: `94.74%`
- Paper ROI at requested 5-share size: `9.59%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260421T202422Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260422T202134Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260423T164851Z | 46 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260424T092134Z | 48 | 2 | 2 | 0 | 100.00% | 95.83% | 0.9400 | 6.38% |
| 20260424T175300Z | 42 | 2 | 2 | 0 | 100.00% | 95.24% | 0.9800 | 2.04% |
| 20260425T092927Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260427T092525Z | 33 | 2 | 2 | 0 | 100.00% | 93.94% | 0.9800 | 2.04% |
| 20260427T141724Z | 16 | 1 | 1 | 0 | 100.00% | 93.75% | 0.9800 | 2.04% |
| 20260427T185610Z | 48 | 6 | 6 | 0 | 100.00% | 87.50% | 0.9717 | 2.92% |
| 20260428T060825Z | 48 | 1 | 1 | 0 | 100.00% | 97.92% | 0.9100 | 9.89% |
| 20260428T133101Z | 46 | 4 | 4 | 0 | 100.00% | 91.30% | 0.9650 | 3.63% |
| 20260429T060822Z | 48 | 3 | 3 | 0 | 100.00% | 93.75% | 0.9667 | 3.45% |
| 20260429T151244Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260429T151512Z | 36 | 4 | 4 | 0 | 100.00% | 88.89% | 0.9800 | 2.04% |
| 20260429T215613Z | 48 | 4 | 4 | 0 | 100.00% | 91.67% | 0.9725 | 2.83% |
| 20260430T105555Z | 2 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260430T110859Z | 60 | 1 | 1 | 0 | 100.00% | 98.33% | 0.9800 | 2.04% |
| 20260430T214102Z | 72 | 5 | 5 | 0 | 100.00% | 93.06% | 0.9540 | 4.82% |
| 20260502T204924Z | 72 | 5 | 5 | 0 | 100.00% | 93.06% | 0.5320 | 87.97% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| europe_pre_us | 8 | 8 | 0 | 100.00% | 0.9562 | 4.58% |
| late_us_asia | 5 | 5 | 0 | 100.00% | 0.7540 | 32.63% |
| us_after_hours | 12 | 12 | 0 | 100.00% | 0.8742 | 14.39% |
| us_regular | 15 | 15 | 0 | 100.00% | 0.9727 | 2.81% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
