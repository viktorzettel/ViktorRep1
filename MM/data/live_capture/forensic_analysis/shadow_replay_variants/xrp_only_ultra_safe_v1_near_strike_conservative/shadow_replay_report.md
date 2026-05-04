# Shadow Replay Report

Candidate: `xrp_only_ultra_safe_v1_near_strike_conservative`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_conservative.py`
Sessions scanned: `18`

## Headline

- Completed XRP markets in scanned sessions: `689`
- Shadow orders: `155`
- Known settled orders: `155`
- Wins/losses: `155/0`
- Win rate: `100.00%`
- Average entry: `0.8910`
- Replay skip rate across completed XRP markets: `77.50%`
- Paper ROI at requested 5-share size: `12.24%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260421T202422Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260422T202134Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260423T164851Z | 46 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260424T092134Z | 48 | 9 | 9 | 0 | 100.00% | 81.25% | 0.9056 | 10.43% |
| 20260424T175300Z | 42 | 15 | 15 | 0 | 100.00% | 64.29% | 0.9487 | 5.41% |
| 20260425T092927Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260427T092525Z | 33 | 6 | 6 | 0 | 100.00% | 81.82% | 0.9700 | 3.09% |
| 20260427T141724Z | 16 | 4 | 4 | 0 | 100.00% | 75.00% | 0.9175 | 8.99% |
| 20260427T185610Z | 48 | 18 | 18 | 0 | 100.00% | 62.50% | 0.8467 | 18.11% |
| 20260428T060825Z | 48 | 10 | 10 | 0 | 100.00% | 79.17% | 0.8070 | 23.92% |
| 20260428T133101Z | 46 | 15 | 15 | 0 | 100.00% | 67.39% | 0.9467 | 5.63% |
| 20260429T060822Z | 48 | 9 | 9 | 0 | 100.00% | 81.25% | 0.7478 | 33.73% |
| 20260429T151244Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260429T151512Z | 36 | 14 | 14 | 0 | 100.00% | 61.11% | 0.9186 | 8.86% |
| 20260429T215613Z | 48 | 16 | 16 | 0 | 100.00% | 66.67% | 0.8925 | 12.04% |
| 20260430T105555Z | 2 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260430T110859Z | 60 | 15 | 15 | 0 | 100.00% | 75.00% | 0.9287 | 7.68% |
| 20260430T214102Z | 72 | 24 | 24 | 0 | 100.00% | 66.67% | 0.8717 | 14.72% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| europe_pre_us | 39 | 39 | 0 | 100.00% | 0.8449 | 18.36% |
| late_us_asia | 22 | 22 | 0 | 100.00% | 0.8273 | 20.88% |
| us_after_hours | 35 | 35 | 0 | 100.00% | 0.8926 | 12.04% |
| us_regular | 59 | 59 | 0 | 100.00% | 0.9442 | 5.91% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
