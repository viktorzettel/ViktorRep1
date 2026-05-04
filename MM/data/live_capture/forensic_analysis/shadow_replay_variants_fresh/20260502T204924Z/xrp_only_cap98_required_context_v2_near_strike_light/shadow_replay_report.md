# Shadow Replay Report

Candidate: `xrp_only_cap98_required_context_v2_near_strike_light`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2_near_strike_light.py`
Sessions scanned: `1`

## Headline

- Completed XRP markets in scanned sessions: `72`
- Shadow orders: `42`
- Known settled orders: `42`
- Wins/losses: `42/0`
- Win rate: `100.00%`
- Average entry: `0.6745`
- Replay skip rate across completed XRP markets: `41.67%`
- Paper ROI at requested 5-share size: `48.25%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260502T204924Z | 72 | 42 | 42 | 0 | 100.00% | 41.67% | 0.6745 | 48.25% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| late_us_asia | 22 | 22 | 0 | 100.00% | 0.7050 | 41.84% |
| us_after_hours | 20 | 20 | 0 | 100.00% | 0.6410 | 56.01% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
