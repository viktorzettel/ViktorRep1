# Aggregate Live Capture Analysis

## Scope
- Sessions analyzed: `22`
- Complete non-flat buckets: `1638`
- Joined snapshots: `253407`
- First signal buckets: `1399`
- First signal wins: `1372`
- First signal win rate: `98.1%`
- 95% Wilson interval: `97.2%` to `98.7%`

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
| 20260429T151244Z | Europe/pre-US | 2026-04-29T15:12:49.179000Z to 2026-04-29T15:12:49.179000Z | 2026-04-29 17:12:49 CEST to 2026-04-29 17:12:49 CEST | 2026-04-29 11:12:49 EDT to 2026-04-29 11:12:49 EDT | 0 | 0/0 | - |
| 20260429T151512Z | US regular hours | 2026-04-29T15:15:22.250000Z to 2026-04-29T18:18:32.007000Z | 2026-04-29 17:15:22 CEST to 2026-04-29 20:18:32 CEST | 2026-04-29 11:15:22 EDT to 2026-04-29 14:18:32 EDT | 72 | 62/63 | 98.4% |
| 20260429T215613Z | Europe/pre-US | 2026-04-29T21:56:23.214000Z to 2026-04-30T01:56:25.002000Z | 2026-04-29 23:56:23 CEST to 2026-04-30 03:56:25 CEST | 2026-04-29 17:56:23 EDT to 2026-04-29 21:56:25 EDT | 96 | 84/86 | 97.7% |
| 20260430T105555Z | Europe/pre-US | 2026-04-30T10:56:05.149000Z to 2026-04-30T11:08:47.006000Z | 2026-04-30 12:56:05 CEST to 2026-04-30 13:08:47 CEST | 2026-04-30 06:56:05 EDT to 2026-04-30 07:08:47 EDT | 4 | 0/0 | - |
| 20260430T110859Z | US regular hours | 2026-04-30T11:09:09.499000Z to 2026-04-30T16:09:10.010000Z | 2026-04-30 13:09:09 CEST to 2026-04-30 18:09:10 CEST | 2026-04-30 07:09:09 EDT to 2026-04-30 12:09:10 EDT | 120 | 104/104 | 100.0% |

## Main Findings

1. The first-signal layer was strong in every session. The worst session was still profitable by outcome count, and the session confidence intervals overlap heavily, so there is no clear evidence yet that one time condition is bad.
2. The edge is late-window, not full-bucket. Snapshot accuracy is weak early in the 5-minute bucket and becomes very strong in the final minute.
3. BS remains better than Kou as a broad snapshot probability engine. Kou is useful as a selective late signal, but the current live data does not justify treating Kou as globally superior.
4. The safety and late-policy layers are doing real separation. `GOOD` and `CLEAR` states hold much better than `OK`/`CAREFUL` and `CAUTION` states.
5. `margin_z` is the strongest simple danger feature. Near-strike late-window states remain the main place where the current side fails.

## Model Quality

| Symbol | Model | Snapshots | Accuracy | Brier | Mean prediction | Realized YES |
|---|---:|---:|---:|---:|---:|---:|
| ALL | bs | 253407 | 81.6% | 0.1273 | 50.8% | 51.2% |
| ALL | kou | 253407 | 80.4% | 0.1398 | 50.5% | 51.2% |
| ALL | raw_kou | 247095 | 80.5% | 0.1384 | 50.5% | 51.0% |
| ethusdt | bs | 127951 | 81.0% | 0.1313 | 50.7% | 51.4% |
| ethusdt | kou | 127951 | 79.5% | 0.1423 | 50.1% | 51.4% |
| ethusdt | raw_kou | 124794 | 79.6% | 0.1412 | 50.1% | 51.0% |
| xrpusdt | bs | 125456 | 82.3% | 0.1232 | 50.9% | 51.1% |
| xrpusdt | kou | 125456 | 81.3% | 0.1371 | 51.0% | 51.1% |
| xrpusdt | raw_kou | 122301 | 81.4% | 0.1356 | 50.9% | 51.0% |

## Time Left

| Time left | Kou accuracy | BS accuracy | Snapshots |
|---|---:|---:|---:|
| 0-15s | 95.8% | 95.7% | 24438 |
| 15-30s | 91.7% | 91.5% | 24521 |
| 30-60s | 87.1% | 87.2% | 48939 |
| 60-90s | 82.5% | 82.4% | 48782 |
| 90-120s | 79.0% | 79.9% | 48692 |
| 120-180s | 72.9% | 75.5% | 19387 |
| 180-240s | 63.6% | 68.3% | 19338 |
| 240-300s | 52.5% | 59.7% | 19224 |

## Safety And Policy

| Slice | Level | Hold rate | Wins / N |
|---|---:|---:|---:|
| Safety | GOOD | 90.5% | 138459/152962 |
| Safety | WAIT | 73.8% | 4805/6509 |
| Safety | OK | 69.9% | 36261/51880 |
| Safety | CAREFUL | 68.2% | 24296/35637 |
| Policy | CLEAR | 94.7% | 102301/108076 |
| Policy | NONE | 74.4% | 79072/106296 |
| Policy | CAUTION | 68.8% | 22448/32616 |

## Last 120s Margin Risk

| Margin z | Hold rate | Wins / N |
|---|---:|---:|
| <0.5 | 63.9% | 19186/30014 |
| 0.5-1.0 | 75.3% | 21649/28765 |
| 1.0-1.5 | 85.0% | 19910/23424 |
| 1.5-2.0 | 91.0% | 18394/20211 |
| >=2.0 | 98.0% | 83050/84788 |
| missing | 77.9% | 3955/5080 |

## Persistence

| Threshold | Hold seconds | Signals | Win rate | 95% Wilson interval |
|---:|---:|---:|---:|---:|
| 0.90 | 0 | 1603 | 96.6% | 95.6% to 97.4% |
| 0.90 | 2 | 1519 | 97.5% | 96.6% to 98.2% |
| 0.90 | 4 | 1452 | 98.1% | 97.2% to 98.7% |
| 0.90 | 6 | 1386 | 98.5% | 97.7% to 99.0% |
| 0.90 | 8 | 1334 | 98.7% | 98.0% to 99.2% |
| 0.91 | 0 | 1599 | 96.8% | 95.8% to 97.6% |
| 0.91 | 2 | 1511 | 97.6% | 96.7% to 98.3% |
| 0.91 | 4 | 1441 | 98.1% | 97.2% to 98.7% |
| 0.91 | 6 | 1375 | 98.6% | 97.9% to 99.1% |
| 0.91 | 8 | 1316 | 98.9% | 98.2% to 99.4% |
| 0.95 | 0 | 1576 | 98.0% | 97.2% to 98.6% |
| 0.95 | 2 | 1451 | 99.2% | 98.6% to 99.5% |
| 0.95 | 4 | 1354 | 99.3% | 98.6% to 99.6% |
| 0.95 | 6 | 1254 | 99.6% | 99.1% to 99.8% |
| 0.95 | 8 | 1091 | 99.7% | 99.2% to 99.9% |

Current live rule (`0.91` threshold, `4s` hold): `1413/1441` wins, `98.1%`.

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
| 20260429T151512Z | ethusdt | BUY_NO | 44.7 | 0.0776 | 2.597 | yes | 8 | 0.8799 |
| 20260429T215613Z | ethusdt | BUY_NO | 20.5 | 0.0768 | 4.293 | yes | 3 | 1.5095 |
| 20260429T215613Z | ethusdt | BUY_NO | 15.1 | 0.0660 | 3.831 | yes | 6 | 1.1892 |

## Calibration Error

| Scope | Symbol | Model | Samples | ECE abs gap |
|---|---|---:|---:|---:|
| all | ALL | bs | 253407 | 4.2% |
| all | ALL | kou | 253407 | 7.1% |
| all | ALL | raw_kou | 247095 | 7.0% |
| all | ethusdt | bs | 127951 | 5.0% |
| all | ethusdt | kou | 127951 | 6.3% |
| all | ethusdt | raw_kou | 124794 | 6.2% |
| all | xrpusdt | bs | 125456 | 4.0% |
| all | xrpusdt | kou | 125456 | 7.9% |
| all | xrpusdt | raw_kou | 122301 | 7.7% |
| late90 | ALL | bs | 146875 | 3.3% |
| late90 | ALL | kou | 146875 | 7.3% |
| late90 | ALL | raw_kou | 143255 | 7.1% |
| late90 | ethusdt | bs | 74157 | 3.8% |
| late90 | ethusdt | kou | 74157 | 6.8% |
| late90 | ethusdt | raw_kou | 72346 | 6.6% |
| late90 | xrpusdt | bs | 72718 | 3.3% |
| late90 | xrpusdt | kou | 72718 | 7.8% |
| late90 | xrpusdt | raw_kou | 70909 | 7.6% |

## Jump Sweep

Jump flags were not the strongest separator in these sessions. They are still useful telemetry, but the current evidence points much more clearly to time-left and `margin_z`.

| Symbol | Window | Threshold | Jump-present hold rate | N |
|---|---|---:|---:|---:|
| ethusdt | 10s_10m | 2.0 | 85.6% | 96875 |
| ethusdt | 30s_15m | 2.0 | 86.0% | 89070 |
| ethusdt | 10s_10m | 2.5 | 86.1% | 88185 |
| ethusdt | 10s_10m | 3.5 | 86.7% | 31743 |
| ethusdt | 30s_15m | 3.5 | 87.0% | 7642 |
| ethusdt | 30s_15m | 2.5 | 87.2% | 50670 |

## Visuals

- [first_signal_by_session.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_capture/visuals/first_signal_by_session.png)
- [model_accuracy_by_time_left.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_capture/visuals/model_accuracy_by_time_left.png)
- [safety_hold_quality.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_capture/visuals/safety_hold_quality.png)
- [margin_z_hold_quality_late120.png](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/forensic_analysis/live_capture/visuals/margin_z_hold_quality_late120.png)

## Bottom Line

The four sessions support the current design direction: wait late, require persistence, and let safety/policy veto risky near-strike regimes. They do not yet prove stable time-of-day superiority or production EV, because the sample is still small and outcomes are feed-settled, not real filled trades with Polymarket prices.

More 4-hour sessions are useful, especially targeted sessions by condition: US regular hours, Europe/pre-US, late US/off-hours, and weekend. The goal should be at least 10-20 sessions per condition before making strong time-of-day claims.
