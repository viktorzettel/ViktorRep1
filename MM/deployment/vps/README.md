# Kou Bot VPS Deployment

Target VPS:

- AWS `eu-west-1`
- Ubuntu Server 24.04 LTS
- x86_64
- 2 vCPU / 4 GB RAM
- 30 GiB gp3
- inbound firewall: SSH only

This deployment runs the current validated pipeline:

- `kou_dual_compact_web.py` on `127.0.0.1:8071`
- headless Chromium keeps the dashboard page open
- browser-forwarded `browser-poly-chainlink` source feeds XRP price into the Python engine
- `kou_polymarket_live_capture.py` runs XRP-only dry-run sniper rehearsal
- no live order is sent
- session auto-stops after 4 hours

Tiny live mode is available separately in `deployment/vps/run_4h_live_tiny.sh`.
It can spend real pUSD and refuses to start unless `KOU_I_UNDERSTAND_REAL_MONEY=YES` is set.

## 1. Build Bundle On Mac

From the local repo:

```bash
cd /Users/viktorzettel/Downloads/ViktorAI/MM
bash deployment/vps/build_vps_bundle.sh
```

This creates:

```text
/Users/viktorzettel/Downloads/ViktorAI/MM/kou_vps_bundle.tar.gz
```

## 2. Copy Bundle To VPS

Replace the host with your VPS SSH target:

```bash
scp /Users/viktorzettel/Downloads/ViktorAI/MM/kou_vps_bundle.tar.gz ubuntu@YOUR_VPS_IP:~/
```

## 3. Unpack On VPS

```bash
ssh ubuntu@YOUR_VPS_IP
mkdir -p ~/kou-bot
tar -xzf ~/kou_vps_bundle.tar.gz -C ~/kou-bot
cd ~/kou-bot
```

## 4. Install Runtime

```bash
bash deployment/vps/setup_ubuntu_24_04.sh
```

## 5. Add Secrets

Create `~/kou-bot/.env` on the VPS. Do not commit or upload it anywhere.

The file needs the Polymarket wallet/proxy/API values you already use locally.

### Compare Local And VPS Secrets Safely

Use fingerprints, not raw values. Matching fingerprints mean the `.env` values
are byte-for-byte the same after normal `.env` parsing.

On the Mac:

```bash
cd /Users/viktorzettel/Downloads/ViktorAI/MM
python3 deployment/vps/env_fingerprint.py --env-file .env
```

On the VPS:

```bash
cd ~/kou-bot
.venv/bin/python deployment/vps/env_fingerprint.py --env-file .env
```

Compare the five `sha256:... len:...` lines:

- `POLY_PRIVATE_KEY`
- `POLY_PROXY_ADDRESS`
- `POLY_API_KEY`
- `POLY_API_SECRET`
- `POLY_API_PASSPHRASE`

Do not paste the real `.env` values into chat or commit them.

## 6. Run First VPS 4h Dry-Run

```bash
cd ~/kou-bot
bash deployment/vps/run_4h_dry_run.sh
```

The run writes to:

```text
~/kou-bot/data/live_capture/<SESSION_ID>/
```

It automatically stops after 4 hours.

## 7. Fetch Results Back To Mac

On the Mac:

```bash
scp -r ubuntu@YOUR_VPS_IP:~/kou-bot/data/live_capture/SESSION_ID /Users/viktorzettel/Downloads/ViktorAI/MM/data/live_capture/
```

Then ask Codex to analyze that session.

## Tiny Supervised Live Test

Only run this after the dry-run path is healthy and you are present for the whole test.
Caps are `1 pUSD/order`, `4 pUSD/session`, `4 orders/session`, `FOK` only.

On the VPS:

```bash
cd ~/kou-bot
KOU_I_UNDERSTAND_REAL_MONEY=YES nohup bash deployment/vps/run_4h_live_tiny.sh > vps_live_run.out 2>&1 &
echo $! > vps_live_run.pid
```

Health check:

```bash
cd ~/kou-bot
SESSION_ID=$(cat .kou_current_session)
.venv/bin/python deployment/vps/summarize_session.py data/live_capture/$SESSION_ID
tail -40 vps_live_run.out
```

The live run captures the same research/audit files as dry-run, plus live submit
results when an order is attempted:

- `polymarket_quotes.jsonl`
- `polymarket_grid_signals.jsonl`
- `polymarket_markets.jsonl`
- `polymarket_events.jsonl`
- `shadow_orders.jsonl`
- `sniper_signals.jsonl`
- `sniper_plans.jsonl`
- `sniper_live_results.jsonl`
- `sniper_live_ledger.jsonl`
- `web.log`, `headless_browser.log`, `polymarket_capture.log`

These files are the input for later v3 safety review and possible v4 guard design.

## Quick Health Commands On VPS

Show active session:

```bash
cat ~/kou-bot/.kou_current_session
```

Watch logs:

```bash
SESSION_ID=$(cat ~/kou-bot/.kou_current_session)
tail -f ~/kou-bot/data/live_capture/$SESSION_ID/polymarket_capture.log
```

Summarize current session:

```bash
SESSION_ID=$(cat ~/kou-bot/.kou_current_session)
~/kou-bot/.venv/bin/python ~/kou-bot/deployment/vps/summarize_session.py ~/kou-bot/data/live_capture/$SESSION_ID
```

Stop current run:

```bash
SESSION_ID=$(cat ~/kou-bot/.kou_current_session)
kill $(cat ~/kou-bot/data/live_capture/$SESSION_ID/capture.pid) 2>/dev/null || true
kill $(cat ~/kou-bot/data/live_capture/$SESSION_ID/browser.pid) 2>/dev/null || true
kill $(cat ~/kou-bot/data/live_capture/$SESSION_ID/web.pid) 2>/dev/null || true
```
