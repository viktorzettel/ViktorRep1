# Shadow Replay Report

Candidate: `xrp_only_ultra_safe_v1`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1.py`
Sessions scanned: `1`

## Headline

- Completed XRP markets in scanned sessions: `72`
- Shadow orders: `37`
- Known settled orders: `37`
- Wins/losses: `37/0`
- Win rate: `100.00%`
- Average entry: `0.6865`
- Replay skip rate across completed XRP markets: `48.61%`
- Paper ROI at requested 5-share size: `45.67%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260502T204924Z | 72 | 37 | 37 | 0 | 100.00% | 48.61% | 0.6865 | 45.67% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| late_us_asia | 20 | 20 | 0 | 100.00% | 0.6795 | 47.17% |
| us_after_hours | 17 | 17 | 0 | 100.00% | 0.6947 | 43.95% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
