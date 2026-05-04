# Aggregate Live Capture Analysis

## Scope
- Sessions analyzed: `17`
- Complete non-flat buckets: `1346`
- Joined snapshots: `208129`
- First signal buckets: `1146`
- First signal wins: `1122`
- First signal win rate: `97.9%`
- 95% Wilson interval: `96.9%` to `98.6%`

## Session Coverage

| Session | Condition | UTC window | Berlin window | NY window | Buckets | First signals | Win rate |
|---|---:|---|---|---|---:|---:|---:|
| 20260414T161252Z | US regular hours | 2026-04-14T16:12:52.325000Z to 2026-04-14T20:29:24.013000Z | 2026-04-14 18:12:52 CEST to 2026-04-14 22:29:24 CEST | 2026-04-14 12:12:52 EDT to 2026-04-14 16:29:24 EDT | 101 | 88/90 | 97.8% |
| 20260417T060220Z | Europe/pre-US | 2026-04-17T06:02:20.197000Z to 2026-04-17T10:02:21.028000Z | 2026-04-17 08:02:20 CEST to 2026-04-17 12:02:21 CEST | 2026-04-17 02:02:20 EDT to 2026-04-17 06:02:21 EDT | 94 | 83/83 | 100.0% |
| 20260417T134535Z | US regular hours | 2026-04-17T13:45:35.668000Z to 2026-04-17T17:45:37.006000Z | 2026-04-17 15:45:35 CEST to 2026-04-17 19:45:37 CEST | 2026-04-17 09:45:35 EDT to 2026-04-17 13:45:37 EDT | 96 | 72/75 | 96.0% |
| 20260418T210358Z | weekend | 2026-04-18T21:03:58.013000Z to 2026-04-19T01:03:59.033000Z | 2026-04-18 23:03:58 CEST to 2026-04-19 03:03:59 CEST | 2026-04-18 17:03:58 EDT to 2026-04-18 21:03:59 EDT | 95 | 84/85 | 98.8% |
| 20260419T131037Z | weekend | 2026-04-19T13:10:37.232000Z to 2026-04-19T16:06:20.007000Z | 2026-04-19 15:10:37 CEST to 2026-04-19 18:06:20 CEST | 2026-04-19 09:10:37 EDT to 2026-04-19 12:06:20 EDT | 32 | 18/19 | 94.7% |
| 20260421T202422Z | Europe/pre-US | 2026-04-21T20:24:22.748000Z to 2026-04-22T00:24:23.006000Z | 2026-04-21 22:24:22 CEST to 2026-04-22 02:24:23 CEST | 2026-04-21 16:24:22 EDT to 2026-04-21 20:24:23 EDT | 96 | 85/85 | 100.0% |
| 20260422T202134Z | Europe/pre-US | 2026-04-22T20:21:34.442000Z to 2026-04-23T00:21:36.042000Z | 2026-04-22 22:21:34 CEST to 2026-04-23 02:21:36 CEST | 2026-04-22 16:21:34 EDT to 2026-04-22 20:21:36 EDT | 93 | 76/78 | 97.4% |
| 20260423T164851Z | US regular hours | 2026-04-23T16:48:51.682000Z to 2026-04-23T20:36:42.012000Z | 2026-04-23 18:48:51 CEST to 2026-04-23 22:36:42 CEST | 2026-04-23 12:48:51 EDT to 2026-04-23 16:36:42 EDT | 92 | 73/77 | 94.8% |
| 20260424T092134Z | Europe/pre-US | 2026-04-24T09:21:39.229000Z to 2026-04-24T13:21:40.005000Z | 2026-04-24 11:21:39 CEST to 2026-04-24 15:21:40 CEST | 2026-04-24 05:21:39 EDT to 2026-04-24 09:21:40 EDT | 94 | 84/84 | 100.0% |
| 20260424T175300Z | US regular hours | 2026-04-24T17:53:05.925000Z to 2026-04-24T21:22:35.009000Z | 2026-04-24 19:53:05 CEST to 2026-04-24 23:22:35 CEST | 2026-04-24 13:53:05 EDT to 2026-04-24 17:22:35 EDT | 80 | 69/69 | 100.0% |
| 20260425T092927Z | weekend | 2026-04-25T09:29:32.378000Z to 2026-04-25T13:29:33.011000Z | 2026-04-25 11:29:32 CEST to 2026-04-25 15:29:33 CEST | 2026-04-25 05:29:32 EDT to 2026-04-25 09:29:33 EDT | 0 | 0/0 | - |
| 20260427T092525Z | Europe/pre-US | 2026-04-27T09:25:30.644000Z to 2026-04-27T12:12:10.008000Z | 2026-04-27 11:25:30 CEST to 2026-04-27 14:12:10 CEST | 2026-04-27 05:25:30 EDT to 2026-04-27 08:12:10 EDT | 63 | 50/50 | 100.0% |
| 20260427T141724Z | US regular hours | 2026-04-27T14:17:29.342000Z to 2026-04-27T15:37:50.007000Z | 2026-04-27 16:17:29 CEST to 2026-04-27 17:37:50 CEST | 2026-04-27 10:17:29 EDT to 2026-04-27 11:37:50 EDT | 32 | 26/26 | 100.0% |
| 20260427T185610Z | US overlap | 2026-04-27T18:56:15.097000Z to 2026-04-27T22:56:20.010000Z | 2026-04-27 20:56:15 CEST to 2026-04-28 00:56:20 CEST | 2026-04-27 14:56:15 EDT to 2026-04-27 18:56:20 EDT | 95 | 81/85 | 95.3% |
| 20260428T060825Z | Europe/pre-US | 2026-04-28T06:08:30.900000Z to 2026-04-28T10:08:31.011000Z | 2026-04-28 08:08:30 CEST to 2026-04-28 12:08:31 CEST | 2026-04-28 02:08:30 EDT to 2026-04-28 06:08:31 EDT | 96 | 80/81 | 98.8% |
| 20260428T133101Z | US regular hours | 2026-04-28T13:31:06.916000Z to 2026-04-28T17:23:37.006000Z | 2026-04-28 15:31:06 CEST to 2026-04-28 19:23:37 CEST | 2026-04-28 09:31:06 EDT to 2026-04-28 13:23:37 EDT | 92 | 75/76 | 98.7% |
| 20260429T060822Z | Europe/pre-US | 2026-04-29T06:08:27.201000Z to 2026-04-29T10:08:28.011000Z | 2026-04-29 08:08:27 CEST to 2026-04-29 12:08:28 CEST | 2026-04-29 02:08:27 EDT to 2026-04-29 06:08:28 EDT | 95 | 78/83 | 94.0% |

## Main Findings

1. The first-signal layer was strong in every session. The worst session was still profitable by outcome count, and the session confidence intervals overlap heavily, so there is no clear evidence yet that one time condition is bad.
2. The edge is late-window, not full-bucket. Snapshot accuracy is weak early in the 5-minute bucket and becomes very strong in the final minute.
3. BS remains better than Kou as a broad snapshot probability engine. Kou is useful as a selective late signal, but the current live data does not justify treating Kou as globally superior.
4. The safety and late-policy layers are doing real separation. `GOOD` and `CLEAR` states hold much better than `OK`/`CAREFUL` and `CAUTION` states.
5. `margin_z` is the strongest simple danger feature. Near-strike late-window states remain the main place where the current side fails.

## Model Quality

| Symbol | Model | Snapshots | Accuracy | Brier | Mean prediction | Realized YES |
|---|---:|---:|---:|---:|---:|---:|
| ALL | bs | 208129 | 81.5% | 0.1278 | 51.4% | 51.7% |
| ALL | kou | 208129 | 80.2% | 0.1402 | 51.0% | 51.7% |
| ALL | raw_kou | 203075 | 80.4% | 0.1387 | 51.0% | 51.5% |
| ethusdt | bs | 105312 | 81.2% | 0.1296 | 51.4% | 52.0% |
| ethusdt | kou | 105312 | 79.5% | 0.1413 | 50.7% | 52.0% |
| ethusdt | raw_kou | 102784 | 79.7% | 0.1399 | 50.7% | 51.8% |
| xrpusdt | bs | 102817 | 81.8% | 0.1260 | 51.5% | 51.4% |
| xrpusdt | kou | 102817 | 80.9% | 0.1391 | 51.3% | 51.4% |
| xrpusdt | raw_kou | 100291 | 81.1% | 0.1374 | 51.3% | 51.3% |

## Time Left

| Time left | Kou accuracy | BS accuracy | Snapshots |
|---|---:|---:|---:|
| 0-15s | 95.5% | 95.4% | 20070 |
| 15-30s | 91.3% | 91.1% | 20137 |
| 30-60s | 87.0% | 87.1% | 40217 |
| 60-90s | 82.6% | 82.6% | 40078 |
| 90-120s | 78.6% | 79.7% | 39986 |
| 120-180s | 72.8% | 75.3% | 15907 |
| 180-240s | 63.0% | 67.9% | 15872 |
| 240-300s | 52.1% | 59.3% | 15794 |

## Safety And Policy

| Slice | Level | Hold rate | Wins / N |
|---|---:|---:|---:|
| Safety | GOOD | 90.3% | 112854/125008 |
| Safety | WAIT | 70.6% | 3504/4961 |
| Safety | OK | 70.3% | 30241/43040 |
| Safety | CAREFUL | 68.8% | 20436/29711 |
| Policy | CLEAR | 94.5% | 83820/88685 |
| Policy | NONE | 74.0% | 64348/86916 |
| Policy | CAUTION | 69.6% | 18867/27119 |

## Last 120s Margin Risk

| Margin z | Hold rate | Wins / N |
|---|---:|---:|
| <0.5 | 65.0% | 16298/25076 |
| 0.5-1.0 | 75.0% | 18040/24049 |
| 1.0-1.5 | 84.6% | 16079/19004 |
| 1.5-2.0 | 91.2% | 15222/16698 |
| >=2.0 | 97.9% | 67740/69199 |
| missing | 75.1% | 2887/3845 |

## Persistence

| Threshold | Hold seconds | Signals | Win rate | 95% Wilson interval |
|---:|---:|---:|---:|---:|
| 0.90 | 0 | 1316 | 96.4% | 95.2% to 97.2% |
| 0.90 | 2 | 1242 | 97.3% | 96.2% to 98.0% |
| 0.90 | 4 | 1187 | 97.9% | 96.9% to 98.6% |
| 0.90 | 6 | 1128 | 98.4% | 97.5% to 99.0% |
| 0.90 | 8 | 1086 | 98.7% | 97.8% to 99.2% |
| 0.91 | 0 | 1312 | 96.5% | 95.4% to 97.4% |
| 0.91 | 2 | 1234 | 97.4% | 96.4% to 98.2% |
| 0.91 | 4 | 1180 | 97.9% | 96.9% to 98.6% |
| 0.91 | 6 | 1118 | 98.6% | 97.7% to 99.1% |
| 0.91 | 8 | 1071 | 98.9% | 98.1% to 99.4% |
| 0.95 | 0 | 1292 | 97.8% | 96.9% to 98.5% |
| 0.95 | 2 | 1188 | 99.0% | 98.2% to 99.4% |
| 0.95 | 4 | 1105 | 99.1% | 98.3% to 99.5% |
| 0.95 | 6 | 1021 | 99.5% | 98.9% to 99.8% |
| 0.95 | 8 | 894 | 99.7% | 99.0% to 99.9% |

Current live rule (`0.91` threshold, `4s` hold): `1155/1180` wins, `97.9%`.

## First-Signal Losses

All observed first-signal losses were XRP buckets. The repeated pattern is not weak safety labels; it is late chop/crossing after an otherwise strong-looking signal.

| Session | Symbol | Signal | Time left | Kou yes | Margin z | Settled | Crosses | Final delta bps |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 20260414T161252Z | xrpusdt | BUY_NO | 62.2 | 0.0606 | 1.581 | yes | 6 | 2.1914 |
| 20260414T161252Z | xrpusdt | BUY_YES | 9.7 | 0.9450 | 1.407 | no | 2 | -2.2004 |
| 20260417T134535Z | xrpusdt | BUY_NO | 48.1 | 0.0722 | 3.080 | yes | 9 | 4.7052 |
| 20260417T134535Z | xrpusdt | BUY_YES | 32.7 | 0.9448 | 2.486 | no | 7 | -16.2042 |
| 20260417T134535Z | xrpusdt | BUY_YES | 25.3 | 0.9234 | 2.467 | no | 6 | -0.6706 |
| 20260418T210358Z | xrpusdt | BUY_YES | 23.3 | 0.9378 | 1.105 | no | 6 | -2.0954 |
| 20260419T131037Z | ethusdt | BUY_NO | 21.8 | 0.0806 | 3.154 | yes | 4 | 2.2657 |
| 20260422T202134Z | ethusdt | BUY_YES | 16.1 | 0.9404 | 2.524 | no | 4 | -2.1686 |
| 20260422T202134Z | xrpusdt | BUY_NO | 1.1 | 0.0046 | 1.908 | yes | 15 | 0.6991 |
| 20260423T164851Z | ethusdt | BUY_NO | 29.3 | 0.0534 | 1.511 | yes | 5 | 3.5496 |
| 20260423T164851Z | xrpusdt | BUY_YES | 12.3 | 0.9548 | 2.032 | no | 2 | -2.7807 |
| 20260423T164851Z | ethusdt | BUY_YES | 10.3 | 0.9490 | 2.514 | no | 1 | -0.9075 |
| 20260423T164851Z | ethusdt | BUY_YES | 5.2 | 0.9806 | 3.034 | no | 10 | -1.1682 |
| 20260427T185610Z | ethusdt | BUY_YES | 15.3 | 0.9372 | 3.816 | no | 5 | -0.4801 |
| 20260427T185610Z | xrpusdt | BUY_NO | 9.3 | 0.0780 | 1.256 | yes | 11 | 2.1556 |
| 20260427T185610Z | ethusdt | BUY_YES | 14.3 | 0.9326 | 3.258 | no | 2 | -0.6116 |
| 20260427T185610Z | xrpusdt | BUY_YES | 7.7 | 0.9514 | 1.539 | no | 18 | -2.1556 |
| 20260428T060825Z | xrpusdt | BUY_YES | 11.6 | 0.9416 | 3.379 | no | 3 | -0.7189 |
| 20260428T133101Z | xrpusdt | BUY_NO | 10.8 | 0.0856 | 0.838 | yes | 7 | 0.7301 |
| 20260429T060822Z | ethusdt | BUY_YES | 33.8 | 0.9200 | 5.136 | no | 1 | -0.3010 |
| 20260429T060822Z | xrpusdt | BUY_YES | 20.7 | 0.9260 | 2.815 | no | 1 | -4.3073 |
| 20260429T060822Z | ethusdt | BUY_YES | 19.0 | 0.9354 | 3.017 | no | 1 | -0.3440 |
| 20260429T060822Z | xrpusdt | BUY_NO | 12.6 | 0.0488 | 1.986 | yes | 3 | 1.2215 |
| 20260429T060822Z | ethusdt | BUY_NO | 12.0 | 0.0748 | 0.547 | yes | 27 | 0.8536 |

## Calibration Error

| Scope | Symbol | Model | Samples | ECE abs gap |
|---|---|---:|---:|---:|
| all | ALL | bs | 208129 | 4.2% |
| all | ALL | kou | 208129 | 6.9% |
| all | ALL | raw_kou | 203075 | 6.7% |
| all | ethusdt | bs | 105312 | 5.0% |
| all | ethusdt | kou | 105312 | 6.3% |
| all | ethusdt | raw_kou | 102784 | 6.2% |
| all | xrpusdt | bs | 102817 | 4.0% |
| all | xrpusdt | kou | 102817 | 7.6% |
| all | xrpusdt | raw_kou | 100291 | 7.5% |
| late90 | ALL | bs | 120661 | 3.3% |
| late90 | ALL | kou | 120661 | 7.2% |
| late90 | ALL | raw_kou | 117767 | 7.1% |
| late90 | ethusdt | bs | 61050 | 3.8% |
| late90 | ethusdt | kou | 61050 | 6.9% |
| late90 | ethusdt | raw_kou | 59602 | 6.7% |
| late90 | xrpusdt | bs | 59611 | 3.4% |
| late90 | xrpusdt | kou | 59611 | 7.6% |
| late90 | xrpusdt | raw_kou | 58165 | 7.4% |

## Jump Sweep

Jump flags were not the strongest separator in these sessions. They are still useful telemetry, but the current evidence points much more clearly to time-left and `margin_z`.

| Symbol | Window | Threshold | Jump-present hold rate | N |
|---|---|---:|---:|---:|
| ethusdt | 10s_10m | 2.0 | 85.8% | 79656 |
| ethusdt | 10s_10m | 2.5 | 86.3% | 72546 |
| ethusdt | 30s_15m | 2.0 | 86.3% | 73269 |
| ethusdt | 10s_10m | 3.5 | 86.7% | 26704 |
| xrpusdt | 30s_15m | 2.0 | 86.9% | 71575 |
| xrpusdt | 10s_10m | 2.5 | 87.1% | 68712 |

## Visuals

- [first_signal_by_session.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_aggregate/visuals/first_signal_by_session.png)
- [model_accuracy_by_time_left.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_aggregate/visuals/model_accuracy_by_time_left.png)
- [safety_hold_quality.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_aggregate/visuals/safety_hold_quality.png)
- [margin_z_hold_quality_late120.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_aggregate/visuals/margin_z_hold_quality_late120.png)

## Bottom Line

The four sessions support the current design direction: wait late, require persistence, and let safety/policy veto risky near-strike regimes. They do not yet prove stable time-of-day superiority or production EV, because the sample is still small and outcomes are feed-settled, not real filled trades with Polymarket prices.

More 4-hour sessions are useful, especially targeted sessions by condition: US regular hours, Europe/pre-US, late US/off-hours, and weekend. The goal should be at least 10-20 sessions per condition before making strong time-of-day claims.
