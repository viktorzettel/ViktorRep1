# Shadow Replay Report

Candidate: `xrp_only_ultra_safe_v1_near_strike_exec_guard`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py`
Sessions scanned: `1`

## Headline

- Completed XRP markets in scanned sessions: `48`
- Shadow orders: `4`
- Known settled orders: `4`
- Wins/losses: `4/0`
- Win rate: `100.00%`
- Average entry: `0.8675`
- Replay skip rate across completed XRP markets: `91.67%`
- Paper ROI at requested 5-share size: `15.27%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260504T201524Z | 48 | 4 | 4 | 0 | 100.00% | 91.67% | 0.8675 | 15.27% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| us_after_hours | 4 | 4 | 0 | 100.00% | 0.8675 | 15.27% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
