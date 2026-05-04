# Shadow Replay Report

Candidate: `xrp_only_cap98_required_context_v2_near_strike_conservative`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2_near_strike_conservative.py`
Sessions scanned: `18`

## Headline

- Completed XRP markets in scanned sessions: `689`
- Shadow orders: `189`
- Known settled orders: `189`
- Wins/losses: `187/2`
- Win rate: `98.94%`
- Average entry: `0.8975`
- Replay skip rate across completed XRP markets: `72.57%`
- Paper ROI at requested 5-share size: `10.24%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260421T202422Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260422T202134Z | 48 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260423T164851Z | 46 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260424T092134Z | 48 | 16 | 16 | 0 | 100.00% | 66.67% | 0.9031 | 10.73% |
| 20260424T175300Z | 42 | 15 | 15 | 0 | 100.00% | 64.29% | 0.9473 | 5.56% |
| 20260425T092927Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260427T092525Z | 33 | 7 | 7 | 0 | 100.00% | 78.79% | 0.9614 | 4.01% |
| 20260427T141724Z | 16 | 6 | 6 | 0 | 100.00% | 62.50% | 0.9267 | 7.91% |
| 20260427T185610Z | 48 | 20 | 20 | 0 | 100.00% | 58.33% | 0.8580 | 16.55% |
| 20260428T060825Z | 48 | 12 | 12 | 0 | 100.00% | 75.00% | 0.8325 | 20.12% |
| 20260428T133101Z | 46 | 18 | 18 | 0 | 100.00% | 60.87% | 0.9489 | 5.39% |
| 20260429T060822Z | 48 | 13 | 12 | 1 | 92.31% | 72.92% | 0.8108 | 13.85% |
| 20260429T151244Z | 0 | 0 | 0 | 0 | - | - | None | - |
| 20260429T151512Z | 36 | 17 | 17 | 0 | 100.00% | 52.78% | 0.9271 | 7.87% |
| 20260429T215613Z | 48 | 17 | 17 | 0 | 100.00% | 64.58% | 0.8924 | 12.06% |
| 20260430T105555Z | 2 | 0 | 0 | 0 | - | 100.00% | None | - |
| 20260430T110859Z | 60 | 18 | 18 | 0 | 100.00% | 70.00% | 0.9322 | 7.27% |
| 20260430T214102Z | 72 | 30 | 29 | 1 | 96.67% | 58.33% | 0.8733 | 10.69% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| europe_pre_us | 55 | 54 | 1 | 98.18% | 0.8687 | 13.02% |
| late_us_asia | 27 | 26 | 1 | 96.30% | 0.8333 | 15.56% |
| us_after_hours | 39 | 39 | 0 | 100.00% | 0.8979 | 11.36% |
| us_regular | 68 | 68 | 0 | 100.00% | 0.9460 | 5.70% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
