# Reversal Regime Veto Audit

This report collapses Polymarket grid rows to one observable `session x asset x bucket x side` cluster before evaluating regime risk.

## Baseline

- clusters: `956`
- wins: `924`
- losses: `32`
- cluster win rate: `96.65%`
- Wilson low/high: `95.31%` / `97.62%`

## Loss Shape

- loss clusters with prior 60s crossing: `17` / `32`
- loss clusters with prior 60s adverse share above 35%: `8` / `32`
- loss clusters with margin_z below 2.0: `14` / `32`

Interpretation: reversal regimes are partly observable through prior crossing, adverse-share, and low margin-z. A minority of losses still looked clean before failure, so a veto layer can reduce loss frequency but cannot eliminate all losses.

## Top Single-Feature Risk Cells

| feature | value | clusters | losses | loss_rate | win_rate | ci_low |
| --- | --- | --- | --- | --- | --- | --- |
| asset | xrp | 478 | 17 | 0.0356 | 0.9644 | 0.9438 |
| asset | eth | 478 | 15 | 0.0314 | 0.9686 | 0.9489 |
| side | yes | 483 | 19 | 0.0393 | 0.9607 | 0.9394 |
| side | no | 473 | 13 | 0.0275 | 0.9725 | 0.9535 |
| time_left_band | 00-10s | 155 | 9 | 0.0581 | 0.9419 | 0.8933 |
| time_left_band | 10-30s | 522 | 18 | 0.0345 | 0.9655 | 0.9462 |
| time_left_band | 30-60s | 218 | 4 | 0.0183 | 0.9817 | 0.9538 |
| time_left_band | 60-90s | 61 | 1 | 0.0164 | 0.9836 | 0.9128 |
| margin_z_band | <1.0 | 24 | 3 | 0.1250 | 0.8750 | 0.6900 |
| margin_z_band | 1.0-1.5 | 69 | 6 | 0.0870 | 0.9130 | 0.8230 |
| margin_z_band | 1.5-2.0 | 108 | 5 | 0.0463 | 0.9537 | 0.8962 |
| margin_z_band | 2.0-3.0 | 184 | 7 | 0.0380 | 0.9620 | 0.9236 |

## Top Multi-Feature Regime Cells

| asset | side | time_left_band | margin_z_band | cross_60s_band | adverse_60s_band | clusters | losses | loss_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| xrp | yes | 10-30s | 2.0-3.0 | 0 | 0-5% | 27 | 1 | 0.0370 |
| eth | yes | 10-30s | >=3.0 | 0 | 0-5% | 85 | 3 | 0.0353 |
| eth | no | 30-60s | >=3.0 | 0 | 0-5% | 35 | 1 | 0.0286 |
| eth | yes | 30-60s | >=3.0 | 0 | 0-5% | 38 | 1 | 0.0263 |
| xrp | yes | 10-30s | >=3.0 | 0 | 0-5% | 74 | 1 | 0.0135 |
| xrp | no | 10-30s | >=3.0 | 0 | 0-5% | 74 | 0 | 0.0000 |
| eth | no | 10-30s | >=3.0 | 0 | 0-5% | 66 | 0 | 0.0000 |
| xrp | no | 30-60s | >=3.0 | 0 | 0-5% | 38 | 0 | 0.0000 |
| xrp | yes | 30-60s | >=3.0 | 0 | 0-5% | 37 | 0 | 0.0000 |

## Candidate Hard-Veto Profiles

| profile | rule | allowed | allowed_frac | losses | win_rate | ci_low | paper_roi | losses_avoided | winners_blocked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| balanced | margin_z>=1.5 and zchg60>=1 | 729 | 0.7626 | 16 | 0.9781 | 0.9646 | 0.0668 | 16 | 211 |
| balanced | margin_z>=1.5 and adv60<=0.75 and cross60<=4 and zchg60>=1 | 713 | 0.7458 | 13 | 0.9818 | 0.9691 | 0.0677 | 19 | 224 |
| balanced | margin_z>=1.5 and cross30<=2 and zchg60>=1 | 713 | 0.7458 | 14 | 0.9804 | 0.9673 | 0.0660 | 18 | 225 |
| balanced | margin_z>=1.5 and adv60<=0.75 and zchg60>=1 | 722 | 0.7552 | 15 | 0.9792 | 0.9660 | 0.0679 | 17 | 217 |
| balanced | margin_z>=1.5 and cross60<=4 and zchg60>=1 | 718 | 0.7510 | 14 | 0.9805 | 0.9675 | 0.0666 | 18 | 220 |
| selective_halfish | adv60<=0.05 and zchg60>=1 and time_left>=5 | 600 | 0.6276 | 8 | 0.9867 | 0.9739 | 0.0550 | 24 | 332 |
| selective_halfish | adv60<=0.05 and cross60<=4 and zchg60>=1 and time_left>=5 | 600 | 0.6276 | 8 | 0.9867 | 0.9739 | 0.0550 | 24 | 332 |
| selective_halfish | adv60<=0.05 and cross30<=2 and zchg60>=1 and time_left>=5 | 600 | 0.6276 | 8 | 0.9867 | 0.9739 | 0.0550 | 24 | 332 |
| selective_halfish | adv60<=0.05 and cross30<=2 and cross60<=4 and zchg60>=1 and time_left>=5 | 600 | 0.6276 | 8 | 0.9867 | 0.9739 | 0.0550 | 24 | 332 |
| selective_halfish | adv30<=0.75 and adv60<=0.05 and zchg60>=1 and time_left>=5 | 600 | 0.6276 | 8 | 0.9867 | 0.9739 | 0.0550 | 24 | 332 |
| very_selective | adv60<=0.05 and prob>=0.92 | 233 | 0.2437 | 7 | 0.9700 | 0.9393 | 0.1039 | 25 | 698 |
| very_selective | adv60<=0.05 and cross60<=4 and prob>=0.92 | 233 | 0.2437 | 7 | 0.9700 | 0.9393 | 0.1039 | 25 | 698 |
| very_selective | adv60<=0.05 and cross30<=2 and prob>=0.92 | 233 | 0.2437 | 7 | 0.9700 | 0.9393 | 0.1039 | 25 | 698 |
| very_selective | adv60<=0.05 and cross30<=2 and cross60<=4 and prob>=0.92 | 233 | 0.2437 | 7 | 0.9700 | 0.9393 | 0.1039 | 25 | 698 |
| very_selective | adv30<=0.75 and adv60<=0.05 and prob>=0.92 | 233 | 0.2437 | 7 | 0.9700 | 0.9393 | 0.1039 | 25 | 698 |

## Production Reading

- It is reasonable to skip whole regimes, even if that removes many markets.
- The safest next research path is to validate a selective veto candidate out-of-sample, not to trade live.
- Rules that skip roughly half the clusters should be treated as capital-preservation candidates, not final production logic.
