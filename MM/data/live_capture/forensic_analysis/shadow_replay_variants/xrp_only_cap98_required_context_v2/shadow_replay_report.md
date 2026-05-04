# Shadow Replay Report

Candidate: `xrp_only_cap98_required_context_v2`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2.py`
Sessions scanned: `18`

## Headline

- Completed XRP markets in scanned sessions: `689`
- Shadow orders: `235`
- Known settled orders: `235`
- Wins/losses: `232/3`
- Win rate: `98.72%`
- Average entry: `0.8808`
- Replay skip rate across completed XRP markets: `65.89%`
- Paper ROI at requested 5-share size: `12.08%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260421T202422Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260422T202134Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260423T164851Z | 46 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260424T092134Z | 48 | 19 | 19 | 0 | 100.00% | 60.42% | 0.8995 | 11.18% |
| 20260424T175300Z | 42 | 16 | 16 | 0 | 100.00% | 61.90% | 0.9012 | 10.96% |
| 20260425T092927Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260427T092525Z | 33 | 12 | 12 | 0 | 100.00% | 63.64% | 0.9142 | 9.39% |
| 20260427T141724Z | 16 | 6 | 6 | 0 | 100.00% | 62.50% | 0.9267 | 7.91% |
| 20260427T185610Z | 48 | 28 | 28 | 0 | 100.00% | 41.67% | 0.8593 | 16.38% |
| 20260428T060825Z | 48 | 19 | 19 | 0 | 100.00% | 60.42% | 0.7900 | 26.58% |
| 20260428T133101Z | 46 | 21 | 21 | 0 | 100.00% | 54.35% | 0.9300 | 7.53% |
| 20260429T060822Z | 48 | 21 | 19 | 2 | 90.48% | 56.25% | 0.8162 | 10.85% |
| 20260429T151244Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260429T151512Z | 36 | 19 | 19 | 0 | 100.00% | 47.22% | 0.9032 | 10.72% |
| 20260429T215613Z | 48 | 19 | 19 | 0 | 100.00% | 60.42% | 0.8826 | 13.30% |
| 20260430T105555Z | 2 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260430T110859Z | 60 | 19 | 19 | 0 | 100.00% | 68.33% | 0.9337 | 7.10% |
| 20260430T214102Z | 72 | 36 | 35 | 1 | 97.22% | 50.00% | 0.8761 | 10.97% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| europe_pre_us | 79 | 77 | 2 | 97.47% | 0.8511 | 14.52% |
| late_us_asia | 30 | 29 | 1 | 96.67% | 0.8420 | 14.81% |
| us_after_hours | 50 | 50 | 0 | 100.00% | 0.8770 | 14.03% |
| us_regular | 76 | 76 | 0 | 100.00% | 0.9295 | 7.59% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
