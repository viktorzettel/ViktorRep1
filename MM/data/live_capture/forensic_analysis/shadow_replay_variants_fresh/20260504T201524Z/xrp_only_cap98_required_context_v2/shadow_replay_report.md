# Shadow Replay Report

Candidate: `xrp_only_cap98_required_context_v2`
Candidate path: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_cap98_required_context_v2.py`
Sessions scanned: `1`

## Headline

- Completed XRP markets in scanned sessions: `48`
- Shadow orders: `14`
- Known settled orders: `14`
- Wins/losses: `13/1`
- Win rate: `92.86%`
- Average entry: `0.9229`
- Replay skip rate across completed XRP markets: `70.83%`
- Paper ROI at requested 5-share size: `0.62%`

Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.

## By Session

| session_id | completed_xrp_markets | shadow_orders | wins | losses | win_rate | skip_rate_xrp_markets | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260504T201524Z | 48 | 14 | 13 | 1 | 92.86% | 70.83% | 0.9229 | 0.62% |

## By UTC Time Window

| time_window_utc | orders | wins | losses | win_rate | avg_entry | paper_roi_requested_size |
| --- | --- | --- | --- | --- | --- | --- |
| late_us_asia | 2 | 2 | 0 | 100.00% | 0.9650 | 3.63% |
| us_after_hours | 12 | 11 | 1 | 91.67% | 0.9158 | 0.09% |

## Output Files

- `shadow_replay_orders.jsonl`
- `shadow_replay_settlements.jsonl`
- `shadow_replay_settlements.csv`
- `shadow_replay_session_summary.csv`
- `shadow_replay_time_window_summary.csv`
