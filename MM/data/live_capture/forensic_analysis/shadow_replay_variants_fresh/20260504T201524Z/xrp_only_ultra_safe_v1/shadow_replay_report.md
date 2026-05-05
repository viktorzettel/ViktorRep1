# Shadow Replay Report

Candidate: `xrp_only_ultra_safe_v1`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1.py`
Sessions scanned: `1`

## Headline

- Completed XRP markets in scanned sessions: `48`
- Shadow orders: `12`
- Known settled orders: `12`
- Wins/losses: `11/1`
- Win rate: `91.67%`
- Average entry: `0.9325`
- Replay skip rate across completed XRP markets: `75.00%`
- Paper ROI at requested 5-share size: `-1.70%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260504T201524Z | 48 | 12 | 11 | 1 | 91.67% | 75.00% | 0.9325 | -1.70% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| late_us_asia | 1 | 1 | 0 | 100.00% | 0.9700 | 3.09% |
| us_after_hours | 11 | 10 | 1 | 90.91% | 0.9291 | -2.15% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
