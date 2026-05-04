# Shadow Replay Report

Candidate: `xrp_only_ultra_safe_v1`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1.py`
Sessions scanned: `19`

## Headline

- Completed XRP markets in scanned sessions: `761`
- Shadow orders: `228`
- Known settled orders: `228`
- Wins/losses: `227/1`
- Win rate: `99.56%`
- Average entry: `0.8461`
- Replay skip rate across completed XRP markets: `70.04%`
- Paper ROI at requested 5-share size: `17.67%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260421T202422Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260422T202134Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260423T164851Z | 46 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260424T092134Z | 48 | 14 | 14 | 0 | 100.00% | 70.83% | 0.8664 | 15.42% |
| 20260424T175300Z | 42 | 16 | 16 | 0 | 100.00% | 61.90% | 0.9131 | 9.51% |
| 20260425T092927Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260427T092525Z | 33 | 9 | 9 | 0 | 100.00% | 72.73% | 0.9489 | 5.39% |
| 20260427T141724Z | 16 | 4 | 4 | 0 | 100.00% | 75.00% | 0.9175 | 8.99% |
| 20260427T185610Z | 48 | 23 | 23 | 0 | 100.00% | 52.08% | 0.8604 | 16.22% |
| 20260428T060825Z | 48 | 16 | 16 | 0 | 100.00% | 66.67% | 0.8225 | 21.58% |
| 20260428T133101Z | 46 | 18 | 18 | 0 | 100.00% | 60.87% | 0.9250 | 8.11% |
| 20260429T060822Z | 48 | 15 | 14 | 1 | 93.33% | 68.75% | 0.7200 | 29.63% |
| 20260429T151244Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260429T151512Z | 36 | 15 | 15 | 0 | 100.00% | 58.33% | 0.9113 | 9.73% |
| 20260429T215613Z | 48 | 18 | 18 | 0 | 100.00% | 62.50% | 0.8828 | 13.28% |
| 20260430T105555Z | 2 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260430T110859Z | 60 | 15 | 15 | 0 | 100.00% | 75.00% | 0.9287 | 7.68% |
| 20260430T214102Z | 72 | 28 | 28 | 0 | 100.00% | 61.11% | 0.8814 | 13.45% |
| 20260502T204924Z | 72 | 37 | 37 | 0 | 100.00% | 48.61% | 0.6865 | 45.67% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| europe_pre_us | 59 | 58 | 1 | 98.31% | 0.8273 | 18.83% |
| late_us_asia | 45 | 45 | 0 | 100.00% | 0.7682 | 30.17% |
| us_after_hours | 60 | 60 | 0 | 100.00% | 0.8260 | 21.07% |
| us_regular | 64 | 64 | 0 | 100.00% | 0.9372 | 6.70% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
