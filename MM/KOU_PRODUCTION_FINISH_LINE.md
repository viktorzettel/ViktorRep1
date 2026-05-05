# Kou XRP Production Finish Line

Last updated: 2026-05-03

## Current State

The bot is close to a production-readiness decision, but it is not live-money ready yet.

What works:

- XRP is the only asset still under consideration.
- ETH remains observation-only because entry prices and source alignment are not clean enough.
- The Kou web engine, live capture, Polymarket quote capture, and shadow settlement loop work as a read-only research stack.
- Historical shadow replay can now compare multiple safety candidates against the same captured markets.
- The new XRP price audit page exists for visual Coinbase Advanced vs Polymarket/Chainlink checking: [xrp_live_price_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/xrp_live_price_web.py).
- The Kou web engine has a direct Python Polymarket RTDS source alias, `poly-chainlink`, but it is not validated for live 1s updates yet.

Source decision:

- Likely leaning Polymarket/Chainlink for XRP after visual inspection.
- User observation on 2026-05-02: Polymarket/Chainlink is almost exact, with only a very short lag.
- Coinbase Advanced is still the default wired source until the next shadow command is chosen.
- `browser-poly-chainlink` still exists as the old browser-forwarded source.
- New direct source: `poly-chainlink` / `polymarket-chainlink` in [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py).
- Direct probe on 2026-05-03 received fresh initial RTDS snapshot rows without a browser, but the 60s test did not receive continuous live 1s updates.
- 60s direct-source age probe: `60` samples, `0` missing seconds, but only `1` unique price; mean age `30.957s`, p95 `57.486s`, max `60.490s`.
- Raw socket diagnostic showed initial rows with fresh ages around `1-2s`, then timeouts instead of steady live updates.
- Current conclusion: `browser-poly-chainlink` remains the validated source for the next serious shadow session. `poly-chainlink` is a promising but incomplete no-browser adapter.

## Best Candidate So Far

Current active pointer:

- [analysis/autoresearch_kou/candidate.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidate.py) still points to `xrp_only_cap98_required_context_v2`.

Best balanced candidate:

- `xrp_only_ultra_safe_v1`
- Replay: `190/191`, ROI `13.42%`
- Better ROI and more trades than the hard-veto version, but still had one replay loss.

Best safety-first research candidate:

- [xrp_only_ultra_safe_v1_near_strike_conservative.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_conservative.py)
- Replay: `155/155`, ROI `12.24%`
- Hard veto:
  - `time_left <= 30s` and `abs(price - strike) < 0.0004` => skip
  - `time_left <= 10s` and `abs(price - strike) < 0.0006` => skip

Best execution-realism challenger:

- [xrp_only_ultra_safe_v1_near_strike_exec_guard.py](/Users/viktorzettel/Downloads/ViktorAI/MM/analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py)
- Wraps ultra-safe + conservative near-strike veto, then adds:
  - `source_age_s <= 3s` and `model_age_s <= 3s`
  - `fill_status = full`
  - visible book ask and visible ask size for the intended paper size
  - executable entry uses `book_ask_price`, not the softer CLOB `/price` endpoint
  - `book_ask - endpoint_buy_price <= 0.03`
- Fresh replay on `20260502T204924Z`: `5/5`, no losses, avg executable entry `0.5320`, ROI `87.97%`.
- Aggregate replay across `19` sessions: `40/40`, no losses, avg executable entry `0.9125`, ROI `9.59%`.
- Interpretation: this is not yet the active strategy; it is the first production-realism filter. It removes many theoretical winners, but it blocks non-executable or suspicious quote states.

Replay comparison:

```text
Candidate                  Trades   Wins/Losses   ROI
v2                         235      232/3         12.08%
v2 + light veto            210      207/3         10.51%
v2 + conservative veto     189      187/2         10.24%
ultra-safe                 191      190/1         13.42%
ultra-safe + light veto    175      174/1         12.97%
ultra-safe + conservative  155      155/0         12.24%
```

Easy read:

- Light veto did not help; it only removed winners.
- Conservative veto helped safety, but costs many trades.
- For pure signal-safety validation, use ultra-safe plus conservative veto.
- For production feasibility validation, use the exec-guard challenger.
- The replay conclusion is therefore not "hard veto everywhere"; it is "ultra-safe is the main improvement, conservative near-strike veto is the safety-first production challenger."

## Next Step

1. Run one fresh 4-6h shadow session using `browser-poly-chainlink` for both display and model, because this is still the validated live-update path.
2. Use the execution-realism candidate if the goal is production feasibility:
   - XRP only
   - no real orders
   - `xrp_only_ultra_safe_v1_near_strike_exec_guard.py` as the shadow candidate
3. Review:
   - capture health
   - shadow orders
   - settlements
   - skipped close-to-strike cases
   - stale-source skips / browser RTDS stalls
   - unknown-size / thin-book / endpoint-book disagreement skips
   - whether trade count is still acceptable
4. Only after a clean forward shadow session, discuss a tiny live-money test.

Recommended shadow candidate path:

```text
analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py
```

## Latest Forward Shadow Audit - 2026-05-03

Session reviewed:

- [data/live_capture/20260502T204924Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260502T204924Z)
- Runtime: full `6h`
- Source: `browser-poly-chainlink` for both display and model
- Active shadow candidate: `xrp_only_ultra_safe_v1_near_strike_conservative`

Headline:

```text
completed XRP markets: 72
grid trigger rows:     458
shadow orders:         29
shadow settlements:    29
wins / losses:         29 / 0
requested-size ROI:    47.06%
```

Market alignment audit:

- All `29/29` shadow orders matched the expected Polymarket 5m market slug.
- All `458/458` grid-trigger rows had bucket/slug alignment.
- Market-switch cadence was clean: every current market advanced by exactly one `5m` slot.
- There were `30` quote rows at exact boundary moments with warning alignment, but none became shadow orders.
- Conclusion: the `0.68` average entry was not caused by attaching orders to the wrong 5m market.

Entry / fillability audit:

```text
all active shadow orders:              29/29 wins, avg entry 0.6800, ROI 47.06%
full-fill-only subset:                 16/16 wins, avg entry 0.7013, ROI 42.60%
known-size full-or-partial subset:     18/18 wins, avg entry 0.6894, ROI 45.04%
known visible-size only:               18/18 wins, ROI 41.44%
known visible-size using book ask:     18/18 wins, ROI 22.65%
```

Important interpretation:

- The win/loss result is real for shadow settlement: the selected sides all settled correctly.
- The ROI/entry result is not yet production-grade because `11/29` active shadow orders had `fill_status = unknown_size`.
- Some low entries came from CLOB `/price` while the order book ask was missing or meaningfully higher.
- Example: one `YES` shadow order used endpoint price `0.01` while the visible ask was `0.30`.
- Therefore the current shadow ledger is good for directional safety, but still optimistic for executable fill quality.

Execution fixes implemented on 2026-05-03:

- Candidate rows now carry `source_age_s`, `model_age_s`, `display_source`, `model_source`, `requested_size`, `book_ask_price`, and `book_endpoint_delta`.
- New exec-guard candidate blocks stale sources, `unknown_size`, partial fills, missing book asks, thin visible size, book asks above the trained cap, and large endpoint/book disagreements.
- Exec-guard shadow orders use the visible `book_ask_price` as the executable entry price when the candidate explicitly clears the trade.
- [polymarket_token_sniper.py](/Users/viktorzettel/Downloads/ViktorAI/MM/polymarket_token_sniper.py) now requires signal source age, visible book ask, visible ask size, and a max `book_ask - endpoint_buy_price` tolerance before it will even produce a dry-run-ready plan.

Execution TODO before live money:

- Forward-test the exec-guard candidate on a fresh session, because its historical aggregate replay is very selective: `40` trades across `761` completed XRP markets.
- Decide whether `book_ask - endpoint_buy_price <= 0.03` is the right tolerance or too strict.
- Treat `unknown_size` as non-executable for live money unless a later tiny-size test proves a safer interpretation.
- Do not rely on `/price` alone when the book ask is missing or much higher.
- Report both requested-size ROI and visible-size/book-ask ROI after every future capture.

Poly/Chainlink source-age audit:

```text
median age: 0.4s
p95 age:    1.3s
p99 age:    61.4s
max age:    120.9s
age > 3s:   4.13% of snapshots
age > 60s:  1.02% of snapshots
```

Source TODO before live money:

- Do not use direct `poly-chainlink` for the next serious shadow session until it proves continuous 1s updates.
- Investigate the missing no-browser live-update link: direct RTDS currently gets initial snapshots but not steady incremental ticks.
- Keep the first live-order rule: no live order if `source_age_s > 3s` or `model_age_s > 3s`.
- Add monitoring for RTDS stalls, reconnects, and missing initial snapshots.
- Keep `browser-poly-chainlink` as the validated live capture source for now, and make browser/headless supervision explicit if used for long captures.

User observation to preserve:

- In very clear last-second markets, the UI/order state can become strange: the likely winning token may appear as `0ct` / sold out while the losing token shows `1ct`.
- This is a strong warning that displayed or endpoint prices can become non-executable near expiry.
- Production logic must trust executable book checks over display prices, especially in the final `10-20s`.

## Forward Shadow Audit - 2026-05-04

Session reviewed:

- [data/live_capture/20260504T130507Z](/Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/20260504T130507Z)
- Runtime: about `2h40m`
- Source: `browser-poly-chainlink` for both display and model
- Active shadow candidate: `xrp_only_ultra_safe_v1_near_strike_exec_guard`

Headline:

```text
completed XRP markets: 32
grid trigger rows:     215
shadow orders:         2
shadow settlements:    2
wins / losses:         2 / 0
paper cost:            9.55
paper PnL:             +0.45
paper ROI:             +4.71%
```

Interpretation:

- The two exec-guard trades were both clean `YES` trades and both settled correctly.
- Both had full visible book size and fresh source age (`0.6s` and `0.2s`).
- The lower trade rate is acceptable if it remains this clean; roughly one trade per hour is not a problem during high-volatility windows.
- Source freshness was mixed: median age was strong (`0.4s`), but stale periods still appeared (`p95 58.8s`, max `208.6s`).
- Stale rows did not become shadow orders because the exec guard blocks stale source rows.

Browser-source stability fix after this session:

- Found a likely stale-source cause in [kou_dual_compact_web.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_dual_compact_web.py): the browser bridge forwarded only `payload.value`, while Polymarket RTDS can send batch snapshot rows under `payload.data`.
- Patched the browser bridge to forward the newest row from `payload.data`.
- Relaxed the browser websocket watchdog from `1.2s` to `6s` and stale reconnect threshold from `1s` to `3s` to avoid over-aggressive reconnect churn.
- Next source-health test should verify whether `source_age_s > 3s` periods shrink after restarting the web server with this patch.

## Final Bot Sketch

The live-money bot should be built as two separate parts with a very narrow handoff.

Part 1: Kou decision and safety engine

- Reads the chosen XRP price source, probably Polymarket/Chainlink if the final audit confirms it.
- Maintains the 5m bucket state, warmup, Kou probability, safety score, path metrics, and hard near-strike veto.
- Emits only one clean signal per market bucket:

```text
{
  symbol: "xrpusdt",
  market_slug: "...",
  bucket_end: ...,
  side: "yes" or "no",
  max_entry_price: ...,
  reason: "ultra_safe_near_strike_conservative_clear",
  expires_at: ...,
  source_age_s: ...,
  model_age_s: ...
}
```

Part 2: Polymarket token sniper / execution engine

- Resolves the current Polymarket 5m market and the YES/NO token IDs.
- Checks CLOB V2 market info, tick size, minimum order size, visible ask, spread, and available size.
- Re-checks the signal immediately before buying: bucket, side, time-left, source freshness, near-strike distance, max entry, and one-order-per-bucket idempotency.
- Places a small marketable BUY using CLOB V2, preferably `FOK` or `FAK` with a strict max price so it never chases a bad fill.
- Records submitted order ID, response status, fill quantity, average fill, wallet, token ID, and final settlement.

Sniper foundation added:

- [polymarket_token_sniper.py](/Users/viktorzettel/Downloads/ViktorAI/MM/polymarket_token_sniper.py)
- Current status: dry-run planning only; live order submission is intentionally disabled.
- What it can do now:
  - load a single Kou buy signal JSON object
  - validate symbol, side, max entry, expiry, required source freshness, and time-left
  - resolve the current XRP 5m Polymarket market
  - inspect the chosen YES/NO token quote and visible ask size
  - use visible book ask as the executable dry-run entry
  - reject stale, expired, too-late, too-expensive, thin-book, missing-book, or endpoint/book-disagreement signals
  - print a structured execution plan
- What it does not do yet:
  - sign or submit a CLOB V2 order
  - manage wallet balances, pUSD, approvals, or settlement
  - run as a continuous service
  - consume live Kou signals automatically
  - replace the existing read-only shadow logger

Example signal shape for the sniper foundation:

```json
{
  "symbol": "xrpusdt",
  "side": "yes",
  "max_entry_price": 0.98,
  "market_slug": "xrp-updown-5m-...",
  "bucket_end": 1777595399,
  "reason": "ultra_safe_near_strike_conservative_clear",
  "expires_at": 1777595385,
  "source_age_s": 0.8,
  "model_age_s": 0.8,
  "time_left_s": 20.0,
  "price": 1.3914,
  "strike": 1.3910
}
```

Safety boundaries for the final bot:

- Private keys and API credentials must never be stored in the repo or markdown files.
- Use environment variables, OS keychain, or a dedicated secrets manager.
- Start with a tiny funded wallet only; never use a main wallet for first tests.
- Add hard kill switches:
  - max spend per order
  - max spend per session
  - max one order per 5m bucket
  - no trade if source age is stale
  - no trade if order book is thin
  - no trade if quote is above `max_entry_price`
  - stop after any failed/ambiguous order response
- Keep read-only shadow logging on even after live orders are enabled, so every real order has a matching decision and replay record.

Important implementation note:

- Polymarket production trading now requires the CLOB V2 integration path, not the old V1 order flow.
- The real execution module should use the official V2 SDK if possible, because it handles signing and order submission details more safely than raw manual signing.
- Collateral and approvals must be handled for pUSD before any order placement test.

## Latest Forward Shadow Result

Fresh 4h browser Poly/Chainlink exec-guard session:

- Session: `20260504T201524Z`
- Runtime: full `4h`
- Source: `browser-poly-chainlink` for display and model
- Candidate: `xrp_only_ultra_safe_v1_near_strike_exec_guard`
- Completed XRP markets: `48`
- Kou snapshots: `7,489`
- Polymarket quote rows: `15,830`
- Grid signal rows: `312`
- Active shadow orders: `4`
- Settled shadow orders: `4`
- Result: `4/4` wins
- Average executable entry: `0.8675`
- Paper cost at 5-share size: `17.35`
- Paper PnL: `+2.65`
- Paper ROI: `+15.27%`

Source-health finding:

- Median source age: `0.8s`
- p95 source age: `1.2s`
- Max source age: `4.0s`
- Only `3/7,489` snapshots were older than `3s`
- No long stale-source periods appeared in this run.

Same-session replay comparison:

| Candidate | Trades | Wins/losses | Average entry | Paper ROI |
| --- | ---: | ---: | ---: | ---: |
| `xrp_only_cap98_required_context_v2` | `14` | `13/1` | `0.9229` | `+0.62%` |
| `xrp_only_ultra_safe_v1` | `12` | `11/1` | `0.9325` | `-1.70%` |
| `xrp_only_ultra_safe_v1_near_strike_exec_guard` | `4` | `4/0` | `0.8675` | `+15.27%` |

Interpretation:

- This is the cleanest forward-shadow result so far for the exec-guard candidate.
- The improved browser Poly/Chainlink source looked materially healthier than the previous stale-source run.
- The v2 and plain ultra-safe candidates both replayed one loss from this same session.
- That loss was a `NO` entry around `0.98/0.99` that settled `YES` by only `+1.436 bps`, which is exactly the razor-close expiry class we wanted to avoid.
- The exec guard blocked that loss through executable quote/entry discipline.
- Market alignment check found `0` slug/bucket mismatches across grid rows and shadow orders.
- Trade frequency is low: `4` accepted trades in `4h`, but the accepted trades had full visible book size and fresh source age.

## Supervised Tiny Live-Test Plan

The next milestone is no longer more broad data capture. The next milestone is a tightly capped, supervised live execution rehearsal that can graduate into a tiny real-money session only after the order path proves clean.

Target session:

- Asset: XRP 5m only
- Source: `browser-poly-chainlink`
- Candidate: `xrp_only_ultra_safe_v1_near_strike_exec_guard`
- Runtime: `4h`
- Order size: about `1 pUSD` maximum cost per accepted signal
- Session cap: `4 pUSD` maximum total submitted cost
- Max orders: `4` per session
- Order frequency: max `1` order per Polymarket 5m market
- Mode progression:
  - Step A: dry-run execution rehearsal with live CLOB quote checks and session ledger, no order submission
  - Step B: one manually armed tiny live session after Step A shows clean plans
  - Step C: stop immediately after any failed, partial, ambiguous, stale, or mismatched order response

Hard live caps:

- `max_order_cost <= 1.00`
- `max_session_cost <= 4.00`
- `max_session_orders <= 4`
- one order per `market_slug` / `bucket_end`
- no order if `source_age_s > 3s`
- no order if `model_age_s > 3s`
- no order if signal is expired
- no order if `time_left_s < 5s`
- no order if resolved market slug differs from the signal slug
- no order if market end differs from the signal bucket by more than the configured tolerance
- no order if visible book ask is missing
- no order if visible ask size is below intended order size
- no order if visible book ask is above the signal max entry or sniper max entry
- no order if `book_ask_price - endpoint_buy_price > 0.03`
- no order after an order submit error until the session is manually reviewed

Current implementation status:

- [polymarket_token_sniper.py](/Users/viktorzettel/Downloads/ViktorAI/MM/polymarket_token_sniper.py) now has dry-run live preflight scaffolding for:
  - model-age guard
  - source-age guard
  - session ledger reading
  - one-order-per-bucket guard
  - max session order count
  - max session cost
  - optional Polymarket geoblock endpoint preflight
- CLOB V2 market-order submission is now wired through `py-clob-client-v2`, but only behind explicit live arming:
  - `--live`
  - `--i-understand-real-money`
  - `--session-ledger ...`
- The SDK was installed locally as `py-clob-client-v2==1.0.0`.
- Live submission writes a `live_order_plan` ledger row before submit and a `live_order_submitted` row after the response.
- Ambiguous/failed/partial-looking responses raise a stop-required error and must end the session until manually reviewed.
- The private key pasted in chat should be treated as compromised; use a fresh tiny wallet or rotate/move funds before any live run.

Execution assumptions:

- User reports that the Polymarket wallet has migrated to pUSD automatically.
- Secrets must still stay outside the repo.
- The bot should still run the exchange/platform geoblock endpoint from the actual operating environment as a preflight, even if legal/entity questions are handled outside the code.
- The first live wallet should be a tiny funded wallet or tiny test allocation, not a main wallet.

Next engineering steps:

1. Signal handoff is now wired in [kou_polymarket_live_capture.py](/Users/viktorzettel/Downloads/ViktorAI/MM/kou_polymarket_live_capture.py):
   - `--sniper-mode signal` writes `sniper_signals.jsonl`
   - `--sniper-mode dry-run` writes `sniper_signals.jsonl`, calls the sniper planner, and writes `sniper_plans.jsonl`
   - `--sniper-mode live` can call the live CLOB V2 sniper, but only with `--sniper-live-ack`
2. Run one live execution rehearsal with no submit:
   - real source
   - real Polymarket market resolution
   - real CLOB quote/book checks
   - real session ledger
   - no `--live`
3. Run one explicit `--live --i-understand-real-money` test only after the rehearsal produces a clean plan.
4. Watch the first live order manually and stop after any unexpected response.

Recommended dry-run handoff for the next supervised rehearsal:

```bash
python kou_polymarket_live_capture.py \
  --session-id "$SESSION_ID" \
  --validation-profile \
  --assets xrp \
  --shadow-candidate analysis/autoresearch_kou/candidates/xrp_only_ultra_safe_v1_near_strike_exec_guard.py \
  --sniper-mode dry-run \
  --sniper-order-size 1 \
  --sniper-max-order-cost 1 \
  --sniper-max-session-cost 4 \
  --sniper-max-session-orders 4 \
  --sniper-require-geoblock-clear
```

## Points Still To Decide

Most of the research logic is clear. The remaining unclear points are execution and operations, not the Kou model itself:

- Final XRP source architecture:
  - human observation favors Polymarket/Chainlink
  - browser-assisted `browser-poly-chainlink` is the current validated live-update path
  - direct Python `poly-chainlink` currently proves initial snapshot access, not continuous 1s updates
  - final bot still needs either a fixed no-browser RTDS adapter or a supervised headless browser adapter, plus reconnect metrics and stale-source alerts
- Live execution SDK:
  - production uses CLOB V2 / official `py-clob-client-v2`
  - market order submit is wired but not yet tested against a real tiny order
  - still need to inspect exact fill/status response shape from a real `FOK`/`FAK` attempt
- Signal handoff:
  - current implementation writes JSONL and can call the sniper in-process
  - keep one-order-per-bucket idempotency in the sniper
- Order style:
  - likely `FOK` or `FAK`
  - use strict max price
  - start with tiny size only
- Wallet and secrets:
  - tiny funded wallet only
  - no private keys in repo
  - env/keychain/secrets manager only
- Operational kill switches:
  - max spend per order
  - max spend per session
  - stop after failed/ambiguous order response
  - stop after unexpected source lag or market mismatch
- Fillability realism:
  - require visible size for the intended order
  - block `unknown_size`
  - compare endpoint buy price against book ask before accepting a signal as executable
  - forward-test whether the current `0.03` endpoint/book tolerance is too strict

## Real-Money Blockers

Do not run the supervised live session until these are solved:

- Fresh/rotated tiny wallet or explicit acceptance that the pasted private key is compromised.
- pUSD collateral and wallet approvals confirmed in the exact trading wallet.
- Geoblock endpoint clear from the real operating environment.
- Tiny-size execution test with confirmed fill behavior.
- Signal handoff from the exec-guard candidate into `polymarket_token_sniper.py`.
- Dry-run execution rehearsal with real CLOB V2 quote checks and session ledger.
- Manual operator present for the full tiny session.

The read-only/shadow stack is research-grade. The real-money bot is a separate integration step.
