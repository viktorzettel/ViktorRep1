# Train-Only Entry Curve Builder

- generated at: `2026-04-29T14:35:42Z`
- selected profile: `balanced`
- candidate: `/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/generated_entry_curve_balanced_v1.py`

## Profile Comparison

| profile | split | allowed | wins | losses | win_rate | avg_entry | paper_roi | ci_low_minus_avg_entry |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| safety_first | train | 389/392 | 383 | 6 | 98.46% | 0.9483 | 4.82% | 1.84pp |
| safety_first | validation | 92/92 | 90 | 2 | 97.83% | 0.9507 | 3.74% | -2.65pp |
| safety_first | test | 193/196 | 189 | 4 | 97.93% | 0.9373 | 5.35% | 1.06pp |
| balanced | train | 387/392 | 381 | 6 | 98.45% | 0.9484 | 4.85% | 1.82pp |
| balanced | validation | 91/92 | 89 | 2 | 97.80% | 0.9505 | 3.78% | -2.71pp |
| balanced | test | 192/196 | 188 | 4 | 97.92% | 0.9373 | 5.39% | 1.04pp |
| roi_seek | train | 376/392 | 370 | 6 | 98.40% | 0.9474 | 5.10% | 1.82pp |
| roi_seek | validation | 89/92 | 87 | 2 | 97.75% | 0.9502 | 3.97% | -2.84pp |
| roi_seek | test | 185/196 | 181 | 4 | 97.84% | 0.9358 | 5.86% | 0.99pp |

## Reading

- `safety_first` preserves more sample and avoids overreacting to tiny cheap-entry cells.
- `roi_seek` may look attractive when cheap contracts happen to win, but it is more sample-starved.
- Treat all generated candidates as shadow/paper candidates until they survive future sessions.
