# Shadow Replay Report

Candidate: `xrp_only_ultra_safe_v1_near_strike_light`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_light.py`
Sessions scanned: `18`

## Headline

- Completed XRP markets in scanned sessions: `689`
- Shadow orders: `175`
- Known settled orders: `175`
- Wins/losses: `174/1`
- Win rate: `99.43%`
- Average entry: `0.8802`
- Replay skip rate across completed XRP markets: `74.60%`
- Paper ROI at requested 5-share size: `12.97%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260421T202422Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260422T202134Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260423T164851Z | 46 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260424T092134Z | 48 | 11 | 11 | 0 | 100.00% | 77.08% | 0.8773 | 13.99% |
| 20260424T175300Z | 42 | 16 | 16 | 0 | 100.00% | 61.90% | 0.9131 | 9.51% |
| 20260425T092927Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260427T092525Z | 33 | 7 | 7 | 0 | 100.00% | 78.79% | 0.9543 | 4.79% |
| 20260427T141724Z | 16 | 4 | 4 | 0 | 100.00% | 75.00% | 0.9175 | 8.99% |
| 20260427T185610Z | 48 | 20 | 20 | 0 | 100.00% | 58.33% | 0.8545 | 17.03% |
| 20260428T060825Z | 48 | 12 | 12 | 0 | 100.00% | 75.00% | 0.8325 | 20.12% |
| 20260428T133101Z | 46 | 17 | 17 | 0 | 100.00% | 63.04% | 0.9482 | 5.46% |
| 20260429T060822Z | 48 | 14 | 13 | 1 | 92.86% | 70.83% | 0.7114 | 30.52% |
| 20260429T151244Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260429T151512Z | 36 | 15 | 15 | 0 | 100.00% | 58.33% | 0.9113 | 9.73% |
| 20260429T215613Z | 48 | 18 | 18 | 0 | 100.00% | 62.50% | 0.8828 | 13.28% |
| 20260430T105555Z | 2 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260430T110859Z | 60 | 15 | 15 | 0 | 100.00% | 75.00% | 0.9287 | 7.68% |
| 20260430T214102Z | 72 | 26 | 26 | 0 | 100.00% | 63.89% | 0.8758 | 14.19% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| europe_pre_us | 49 | 48 | 1 | 97.96% | 0.8257 | 18.64% |
| late_us_asia | 23 | 23 | 0 | 100.00% | 0.8291 | 20.61% |
| us_after_hours | 41 | 41 | 0 | 100.00% | 0.8788 | 13.79% |
| us_regular | 62 | 62 | 0 | 100.00% | 0.9431 | 6.04% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
