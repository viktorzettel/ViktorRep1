# Shadow Replay Report

Candidate: `xrp_only_cap98_required_context_v2`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2.py`
Sessions scanned: `1`

## Headline

- Completed XRP markets in scanned sessions: `72`
- Shadow orders: `46`
- Known settled orders: `46`
- Wins/losses: `46/0`
- Win rate: `100.00%`
- Average entry: `0.6715`
- Replay skip rate across completed XRP markets: `36.11%`
- Paper ROI at requested 5-share size: `48.92%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260502T204924Z | 72 | 46 | 46 | 0 | 100.00% | 36.11% | 0.6715 | 48.92% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| late_us_asia | 23 | 23 | 0 | 100.00% | 0.6765 | 47.81% |
| us_after_hours | 23 | 23 | 0 | 100.00% | 0.6665 | 50.03% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
