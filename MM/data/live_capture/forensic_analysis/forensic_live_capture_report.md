# Live Capture Forensic Report

Generated from the current contents of `data/live_capture`.

## Executive Takeaway

The data is useful and directionally strong, but it is not yet enough to call the strategy production-proven. The model-side first-signal layer is consistently strong across eight sessions. The execution-aware Polymarket matrix is the sharper test, and it currently says: XRP looks statistically promising after observed entry prices; ETH is not yet statistically proven after observed entry prices.

The most important forensic detail is that the independent sample unit is not the `6,789` grid rows. Those rows are overlapping threshold/hold views of roughly a few hundred market buckets. Treat the grid as a calibration surface, not thousands of independent trades.

## Dataset
| Metric | Value |
|---|---|
| Kou capture sessions | 17 |
| Complete non-flat buckets | 1346 |
| Joined Kou snapshots | 208129 |
| Polymarket sessions | 17 |
| Polymarket quote rows | 488764 |
| Polymarket grid events | 23945 |

| Session | Condition | PM sidecar | Hours | Buckets | First signals | Win rate |
|---|---|---|---|---|---|---|
| 20260414T161252Z | US regular hours | no | 4.28 | 101 | 88/90 | 97.8% |
| 20260417T060220Z | Europe/pre-US | no | 4.00 | 94 | 83/83 | 100.0% |
| 20260417T134535Z | US regular hours | no | 4.00 | 96 | 72/75 | 96.0% |
| 20260418T210358Z | weekend | no | 4.00 | 95 | 84/85 | 98.8% |
| 20260419T131037Z | weekend | no | 2.93 | 32 | 18/19 | 94.7% |
| 20260421T202422Z | Europe/pre-US | yes | 4.00 | 96 | 85/85 | 100.0% |
| 20260422T202134Z | Europe/pre-US | yes | 4.00 | 93 | 76/78 | 97.4% |
| 20260423T164851Z | US regular hours | yes | 3.80 | 92 | 73/77 | 94.8% |
| 20260424T092134Z | Europe/pre-US | yes | 4.00 | 94 | 84/84 | 100.0% |
| 20260424T175300Z | US regular hours | yes | 3.49 | 80 | 69/69 | 100.0% |
| 20260425T092927Z | weekend | yes | 4.00 | 0 | 0/0 | - |
| 20260427T092525Z | Europe/pre-US | yes | 2.78 | 63 | 50/50 | 100.0% |
| 20260427T141724Z | US regular hours | yes | 1.34 | 32 | 26/26 | 100.0% |
| 20260427T185610Z | US overlap | yes | 4.00 | 95 | 81/85 | 95.3% |
| 20260428T060825Z | Europe/pre-US | yes | 4.00 | 96 | 80/81 | 98.8% |
| 20260428T133101Z | US regular hours | yes | 3.88 | 92 | 75/76 | 98.7% |
| 20260429T060822Z | Europe/pre-US | yes | 4.00 | 95 | 78/83 | 94.0% |

## Model-Side Statistics

Across all sessions the first-signal layer hit `1122/1146` = `97.9%` with a Wilson 95% interval of `96.9%` to `98.6%`. The binomial tail probability of seeing at least this many wins if the true rate were `90%` is about `1.88e-26`; against a `95%` true rate it is `3.33e-07`. That is strong evidence that the late/persistent signal is not random, with the caveat that adjacent 5-minute markets are regime-correlated.

| Symbol | Wins/N | Rate | Wilson 95% |
|---|---|---|---|
| ethusdt | 565/575 | 98.3% | 96.8% to 99.1% |
| xrpusdt | 557/571 | 97.5% | 95.9% to 98.5% |

| Condition | Wins/N | Rate | Wilson 95% |
|---|---|---|---|
| Europe/pre-US | 536/544 | 98.5% | 97.1% to 99.3% |
| US overlap | 81/85 | 95.3% | 88.5% to 98.2% |
| US regular hours | 403/413 | 97.6% | 95.6% to 98.7% |
| weekend | 102/104 | 98.1% | 93.3% to 99.5% |

The current model-side rule in the analyzer, `0.91` confidence with `4s` persistence, shows `1155/1180` = `97.9%` with Wilson interval `96.9%` to `98.6%`.

| Symbol | Model | Snapshots | Accuracy | Brier |
|---|---|---|---|---|
| ALL | bs | 208129 | 81.5% | 0.1278 |
| ALL | kou | 208129 | 80.2% | 0.1402 |
| ALL | raw_kou | 203075 | 80.4% | 0.1387 |
| ethusdt | bs | 105312 | 81.2% | 0.1296 |
| ethusdt | kou | 105312 | 79.5% | 0.1413 |
| ethusdt | raw_kou | 102784 | 79.7% | 0.1399 |
| xrpusdt | bs | 102817 | 81.8% | 0.1260 |
| xrpusdt | kou | 102817 | 80.9% | 0.1391 |
| xrpusdt | raw_kou | 100291 | 81.1% | 0.1374 |

BS is still the better broad snapshot probability engine by Brier score, but the trading edge is not broad-snapshot prediction. The edge is the selective late signal plus persistence and safety filtering.

| Time Left | Kou Accuracy | BS Accuracy | Snapshots |
|---|---|---|---|
| 0-15s | 95.5% | 95.4% | 20070 |
| 15-30s | 91.3% | 91.1% | 20137 |
| 30-60s | 87.0% | 87.1% | 40217 |
| 60-90s | 82.6% | 82.6% | 40078 |
| 90-120s | 78.6% | 79.7% | 39986 |
| 120-180s | 72.8% | 75.3% | 15907 |
| 180-240s | 63.0% | 67.9% | 15872 |
| 240-300s | 52.1% | 59.3% | 15794 |

## Safety Layer
| Safety | Wins/N | Current-side hold rate |
|---|---|---|
| CAREFUL | 20436/29711 | 68.8% |
| GOOD | 112854/125008 | 90.3% |
| OK | 30241/43040 | 70.3% |
| WAIT | 3504/4961 | 70.6% |

| Policy | Wins/N | Current-side hold rate |
|---|---|---|
| CAUTION | 18867/27119 | 69.6% |
| CLEAR | 83820/88685 | 94.5% |
| NONE | 64348/86916 | 74.0% |

| Last 120s margin_z | Wins/N | Hold rate |
|---|---|---|
| 0.5-1.0 | 18040/24049 | 75.0% |
| 1.0-1.5 | 16079/19004 | 84.6% |
| 1.5-2.0 | 15222/16698 | 91.2% |
| <0.5 | 16298/25076 | 65.0% |
| >=2.0 | 67740/69199 | 97.9% |
| missing | 2887/3845 | 75.1% |

`margin_z` remains the clearest safety feature. Late-window states below `1.0` z hold only about two thirds to three quarters of the time, while `>=2.0` z holds around `98%`.

## Polymarket Execution Matrix

The Polymarket-aware sidecar produced `23945` grid events across `17` sessions. On those same three sessions, the model-side first-signal result was `777/794` = `97.9%` with Wilson interval `96.6%` to `98.7%`.

The enhanced matrix with confidence intervals is written to `data/live_capture/forensic_analysis/polymarket_grid/polymarket_grid_matrix_with_ci.csv`.

| Asset | Rule | Wins/N | Win | Wilson 95% | Avg Entry | Win-Entry | Visible Fill | Filled ROI | Filled PnL | p vs entry |
|---|---|---|---|---|---|---|---|---|---|---|
| XRP | 90%/3s | 412/424 | 97.2% | 95.1% to 98.4% | 0.912 | 5.9% | 69.6% | 7.1% | 88.48 | 6.62e-07 |
| XRP | 91%/4s | 399/409 | 97.6% | 95.6% to 98.7% | 0.917 | 5.8% | 63.1% | 8.5% | 94.43 | 7.08e-07 |
| XRP | 95%/2s | 548/555 | 98.7% | 97.4% to 99.4% | 0.911 | 7.7% | 48.5% | 11.8% | 136.22 | 8.37e-15 |
| ETH | 94%/2s | 449/456 | 98.5% | 96.9% to 99.3% | 0.953 | 3.2% | 53.3% | 5.4% | 60.03 | 0.000204 |
| ETH | 96%/4s | 380/382 | 99.5% | 98.1% to 99.9% | 0.965 | 2.9% | 31.9% | 4.8% | 27.22 | 0.000158 |
| ETH | 91%/4s | 403/413 | 97.6% | 95.6% to 98.7% | 0.955 | 2.1% | 63.2% | 2.7% | 32.15 | 0.021 |

Rows where the Wilson lower bound beats average observed entry price: `54`. All of them are XRP rows. ETH has positive-looking rows, but none clear that stricter confidence screen yet.

| Asset | Visible fill rate 90%/2s -> 96%/5s | Avg entry | Win rate |
|---|---|---|---|
| ETH | 67.7% -> 25.7% | 0.939 -> 0.971 | 97.0% -> 99.4% |
| XRP | 72.9% -> 28.5% | 0.904 -> 0.916 | 96.9% -> 100.0% |

Stricter thresholds and longer holds improve apparent win rate, but they also raise entry price and reduce visible fillability. This matters because a token bought at `0.98` needs a very high true win rate; one loss can erase many small wins.

## Quote Capture Health
| Session | Asset | Quotes | Latency med/p95 s | Buy sum med/p95 | Median YES/NO ask size |
|---|---|---|---|---|---|
| 20260421T202422Z | ETH | 20117 | 0.052 / 0.084 | 0.99 / 0.99 | 88.0 / 96.8 |
| 20260421T202422Z | XRP | 20117 | 0.049 / 0.079 | 0.98 / 0.99 | 14.0 / 13.9 |
| 20260422T202134Z | ETH | 20142 | 0.056 / 0.100 | 0.99 / 0.99 | 72.3 / 73.4 |
| 20260422T202134Z | XRP | 20142 | 0.054 / 0.090 | 0.97 / 0.99 | 10.0 / 10.0 |
| 20260423T164851Z | ETH | 18205 | 0.066 / 0.247 | 0.99 / 0.99 | 57.3 / 60.2 |
| 20260423T164851Z | XRP | 18205 | 0.061 / 0.238 | 0.98 / 0.99 | 11.0 / 12.0 |
| 20260424T092134Z | ETH | 20104 | 0.052 / 0.085 | 0.99 / 0.99 | 68.0 / 76.2 |
| 20260424T092134Z | XRP | 20104 | 0.049 / 0.088 | 0.99 / 0.99 | 15.0 / 15.0 |
| 20260424T175300Z | ETH | 17597 | 0.052 / 0.084 | 0.99 / 0.99 | 66.5 / 74.4 |
| 20260424T175300Z | XRP | 17597 | 0.049 / 0.081 | 0.99 / 0.99 | 12.0 / 10.0 |
| 20260425T092927Z | ETH | 17570 | 0.062 / 0.103 | 0.99 / 0.99 | 51.9 / 52.7 |
| 20260425T092927Z | XRP | 17570 | 0.056 / 0.098 | 0.97 / 0.99 | 10.0 / 10.0 |
| 20260427T092525Z | ETH | 13868 | 0.045 / 0.071 | 0.99 / 0.99 | 84.6 / 89.2 |
| 20260427T092525Z | XRP | 13868 | 0.042 / 0.067 | 0.98 / 0.99 | 12.0 / 10.0 |
| 20260427T141724Z | ETH | 6743 | 0.049 / 0.083 | 0.99 / 0.99 | 50.5 / 45.0 |
| 20260427T141724Z | XRP | 6743 | 0.046 / 0.076 | 0.98 / 0.99 | 11.0 / 10.0 |
| 20260427T185610Z | ETH | 20117 | 0.047 / 0.099 | 0.99 / 0.99 | 41.0 / 45.0 |
| 20260427T185610Z | XRP | 20117 | 0.045 / 0.087 | 0.98 / 0.99 | 14.7 / 10.0 |
| 20260428T060825Z | ETH | 20088 | 0.045 / 0.080 | 0.99 / 0.99 | 49.5 / 49.4 |
| 20260428T060825Z | XRP | 20088 | 0.043 / 0.078 | 0.99 / 0.99 | 11.2 / 10.0 |
| 20260428T133101Z | ETH | 19460 | 0.047 / 0.087 | 0.99 / 0.99 | 57.2 / 55.0 |
| 20260428T133101Z | XRP | 19460 | 0.045 / 0.082 | 0.99 / 0.99 | 14.3 / 10.4 |
| 20260429T060822Z | ETH | 20135 | 0.046 / 0.078 | 0.99 / 0.99 | 48.0 / 50.2 |
| 20260429T060822Z | XRP | 20135 | 0.044 / 0.073 | 0.97 / 0.99 | 20.0 / 20.0 |
| 20260429T151512Z | ETH | 12024 | 0.047 / 0.075 | 0.99 / 0.99 | 55.0 / 41.8 |
| 20260429T151512Z | XRP | 12024 | 0.044 / 0.071 | 0.98 / 0.99 | 15.0 / 12.0 |
| 20260429T215613Z | XRP | 15821 | 0.042 / 0.087 | 0.97 / 0.99 | 15.0 / 15.0 |
| 20260430T105555Z | XRP | 813 | 0.045 / 0.087 | 0.96 / 0.99 | 10.0 / 8.4 |
| 20260430T110859Z | XRP | 19790 | 0.046 / 0.093 | 0.98 / 0.99 | 13.7 / 13.1 |

| Session | Event | Count |
|---|---|---|
| 20260421T202422Z | book_fetch_error | 94 |
| 20260421T202422Z | market_discovery_error | 472 |
| 20260422T202134Z | book_fetch_error | 2 |
| 20260422T202134Z | snapshot_fetch_error | 6 |
| 20260422T202134Z | snapshot_parse_error | 1 |
| 20260423T164851Z | book_fetch_error | 2 |
| 20260423T164851Z | snapshot_fetch_error | 1 |
| 20260424T092134Z | book_fetch_error | 10 |
| 20260425T092927Z | book_fetch_error | 2 |
| 20260427T092525Z | book_fetch_error | 3 |
| 20260429T060822Z | book_fetch_error | 2 |
| 20260429T151244Z | snapshot_fetch_error | 69 |
| 20260430T110859Z | book_fetch_error | 2 |

Quote latency is mostly small enough for analysis-grade capture. The data still does not prove personal fills: it observes read-only CLOB prices and visible ask size, not queue position, order acknowledgements, or slippage after submission.

## Failure Pattern

| Session | Symbol | Signal | t-left | Kou yes | margin_z | Settled | Crosses | Final bps |
|---|---|---|---|---|---|---|---|---|
| 20260414T161252Z | xrpusdt | BUY_NO | 62.2 | 0.0606 | 1.581 | yes | 6 | 2.19 |
| 20260414T161252Z | xrpusdt | BUY_YES | 9.7 | 0.9450 | 1.407 | no | 2 | -2.20 |
| 20260417T134535Z | xrpusdt | BUY_NO | 48.1 | 0.0722 | 3.080 | yes | 9 | 4.71 |
| 20260417T134535Z | xrpusdt | BUY_YES | 32.7 | 0.9448 | 2.486 | no | 7 | -16.20 |
| 20260417T134535Z | xrpusdt | BUY_YES | 25.3 | 0.9234 | 2.467 | no | 6 | -0.67 |
| 20260418T210358Z | xrpusdt | BUY_YES | 23.3 | 0.9378 | 1.105 | no | 6 | -2.10 |
| 20260419T131037Z | ethusdt | BUY_NO | 21.8 | 0.0806 | 3.154 | yes | 4 | 2.27 |
| 20260422T202134Z | ethusdt | BUY_YES | 16.1 | 0.9404 | 2.524 | no | 4 | -2.17 |
| 20260422T202134Z | xrpusdt | BUY_NO | 1.1 | 0.0046 | 1.908 | yes | 15 | 0.70 |
| 20260423T164851Z | ethusdt | BUY_NO | 29.3 | 0.0534 | 1.511 | yes | 5 | 3.55 |
| 20260423T164851Z | ethusdt | BUY_YES | 10.3 | 0.9490 | 2.514 | no | 1 | -0.91 |
| 20260423T164851Z | ethusdt | BUY_YES | 5.2 | 0.9806 | 3.034 | no | 10 | -1.17 |
| 20260423T164851Z | xrpusdt | BUY_YES | 12.3 | 0.9548 | 2.032 | no | 2 | -2.78 |
| 20260427T185610Z | ethusdt | BUY_YES | 15.3 | 0.9372 | 3.816 | no | 5 | -0.48 |
| 20260427T185610Z | ethusdt | BUY_YES | 14.3 | 0.9326 | 3.258 | no | 2 | -0.61 |
| 20260427T185610Z | xrpusdt | BUY_NO | 9.3 | 0.0780 | 1.256 | yes | 11 | 2.16 |
| 20260427T185610Z | xrpusdt | BUY_YES | 7.7 | 0.9514 | 1.539 | no | 18 | -2.16 |
| 20260428T060825Z | xrpusdt | BUY_YES | 11.6 | 0.9416 | 3.379 | no | 3 | -0.72 |
| 20260428T133101Z | xrpusdt | BUY_NO | 10.8 | 0.0856 | 0.838 | yes | 7 | 0.73 |
| 20260429T060822Z | ethusdt | BUY_YES | 33.8 | 0.9200 | 5.136 | no | 1 | -0.30 |
| 20260429T060822Z | ethusdt | BUY_YES | 19.0 | 0.9354 | 3.017 | no | 1 | -0.34 |
| 20260429T060822Z | ethusdt | BUY_NO | 12.0 | 0.0748 | 0.547 | yes | 27 | 0.85 |
| 20260429T060822Z | xrpusdt | BUY_YES | 20.7 | 0.9260 | 2.815 | no | 1 | -4.31 |
| 20260429T060822Z | xrpusdt | BUY_NO | 12.6 | 0.0488 | 1.986 | yes | 3 | 1.22 |

The common loss signature is late crossing/chop after a strong-looking signal. Earlier data made this look mostly XRP-specific; the newest run adds several ETH losses too. This argues for a crossing/chop veto, not just stricter probability.

## Significance And Usefulness

Useful: yes. The data is already good enough to reject the idea that the late persistent signal is random, and it is good enough to identify execution-aware XRP candidate rules.

Not enough yet: production EV. The independent sample is closer to hundreds of asset-buckets, not thousands of grid events. The three Polymarket sessions all sit in a narrow calendar window, the rules are highly overlapping, and observed fillability is not the same as real fills.

A practical significance screen is: Wilson lower bound of win rate should exceed average entry price. XRP has multiple rows passing that screen; ETH currently has none. This is the cleanest single result from the execution-aware data.

## Suggested Next Steps

1. Keep running paired 4-hour sessions with both sidecars. Prioritize more Polymarket-aware data, not more model-only data.
2. Treat XRP `0.90-0.93` with `3-4s` persistence as the leading candidate band for paper-trade replay. Keep `0.95/2s` as a stricter comparison.
3. Do not promote ETH live size yet. ETH needs either cheaper observed entries, a stronger veto against late crosses, or more samples proving it can beat `0.96-0.98` entries.
4. Add a candidate veto for repeated strike crossing/chop in the final minute, then rerun the matrix with and without that veto.
5. Start logging actual order attempts in tiny size when ready, because visible ask size and read-only buy price are still only fill proxies.

