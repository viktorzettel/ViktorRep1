# Shadow Replay Report

Candidate: `xrp_only_cap98_required_context_v2_near_strike_light`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2_near_strike_light.py`
Sessions scanned: `18`

## Headline

- Completed XRP markets in scanned sessions: `689`
- Shadow orders: `210`
- Known settled orders: `210`
- Wins/losses: `207/3`
- Win rate: `98.57%`
- Average entry: `0.8920`
- Replay skip rate across completed XRP markets: `69.52%`
- Paper ROI at requested 5-share size: `10.51%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260421T202422Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260422T202134Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260423T164851Z | 46 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260424T092134Z | 48 | 17 | 17 | 0 | 100.00% | 64.58% | 0.9171 | 9.04% |
| 20260424T175300Z | 42 | 16 | 16 | 0 | 100.00% | 61.90% | 0.9012 | 10.96% |
| 20260425T092927Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260427T092525Z | 33 | 8 | 8 | 0 | 100.00% | 75.76% | 0.9487 | 5.40% |
| 20260427T141724Z | 16 | 6 | 6 | 0 | 100.00% | 62.50% | 0.9267 | 7.91% |
| 20260427T185610Z | 48 | 24 | 24 | 0 | 100.00% | 50.00% | 0.8592 | 16.39% |
| 20260428T060825Z | 48 | 13 | 13 | 0 | 100.00% | 72.92% | 0.8400 | 19.05% |
| 20260428T133101Z | 46 | 20 | 20 | 0 | 100.00% | 56.52% | 0.9500 | 5.26% |
| 20260429T060822Z | 48 | 19 | 17 | 2 | 89.47% | 60.42% | 0.8132 | 10.03% |
| 20260429T151244Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260429T151512Z | 36 | 18 | 18 | 0 | 100.00% | 50.00% | 0.9206 | 8.63% |
| 20260429T215613Z | 48 | 19 | 19 | 0 | 100.00% | 60.42% | 0.8826 | 13.30% |
| 20260430T105555Z | 2 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260430T110859Z | 60 | 18 | 18 | 0 | 100.00% | 70.00% | 0.9317 | 7.33% |
| 20260430T214102Z | 72 | 32 | 31 | 1 | 96.88% | 55.56% | 0.8766 | 10.52% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| europe_pre_us | 64 | 62 | 2 | 96.88% | 0.8689 | 11.49% |
| late_us_asia | 28 | 27 | 1 | 96.43% | 0.8346 | 15.53% |
| us_after_hours | 46 | 46 | 0 | 100.00% | 0.8850 | 12.99% |
| us_regular | 72 | 72 | 0 | 100.00% | 0.9392 | 6.48% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
