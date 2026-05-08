---
name: kou-vps-ops
description: Use when managing the Kou Polymarket VPS: SSH from the Mac, deploy the bundle, configure Ubuntu 24.04, start/stop/restart 4h dry-run sessions, monitor browser Poly/Chainlink source health, fetch logs/data, and avoid accidentally killing foreground SSH runs.
---

# Kou VPS Ops

Use this for Kou bot VPS operations. Never print or commit secrets. Treat `.env` and private keys as sensitive.

## Known Setup

- Local repo: `/Users/viktorzettel/Downloads/ViktorAI/MM`
- VPS app dir: `~/kou-bot`
- SSH key on Mac: `/Users/viktorzettel/Downloads/ViktorAI/MM/poly-bot-key.pem`
- Current VPS host: `ubuntu@52.215.220.165`
- VPS region: AWS Ireland `eu-west-1`
- OS: Ubuntu 24.04 x86_64
- Runtime: Python venv at `~/kou-bot/.venv`
- Source mode: headless Chromium keeps `http://127.0.0.1:8071/` open for `browser-poly-chainlink`
- Main session output: `~/kou-bot/data/live_capture/<SESSION_ID>/`

## Mac Sleep Rule

If a run was started in a foreground SSH command, do not tell the user their Mac can sleep safely. SSH disconnect can terminate the foreground shell and stop the run.

For Mac sleep/disconnect safety, start the VPS run detached:

```bash
cd ~/kou-bot
nohup bash deployment/vps/run_4h_dry_run.sh > vps_run.out 2>&1 &
echo $! > vps_run.pid
```

Then the Mac can sleep or disconnect. Monitor later by SSHing back in.

## SSH From Mac

```bash
cd /Users/viktorzettel/Downloads/ViktorAI/MM
chmod 400 poly-bot-key.pem
ssh -i poly-bot-key.pem ubuntu@52.215.220.165
```

## Build And Upload Bundle

On Mac:

```bash
cd /Users/viktorzettel/Downloads/ViktorAI/MM
bash deployment/vps/build_vps_bundle.sh
scp -i poly-bot-key.pem kou_vps_bundle.tar.gz ubuntu@52.215.220.165:~/
```

On VPS:

```bash
mkdir -p ~/kou-bot
tar -xzf ~/kou_vps_bundle.tar.gz -C ~/kou-bot
cd ~/kou-bot
bash deployment/vps/setup_ubuntu_24_04.sh
```

Create `.env` on VPS manually and lock permissions:

```bash
nano ~/kou-bot/.env
chmod 600 ~/kou-bot/.env
```

Check `.env` shape without revealing secrets:

```bash
cd ~/kou-bot
sed 's/=.*/=<hidden>/' .env
```

Compare local and VPS `.env` values without revealing secrets:

On Mac:

```bash
cd /Users/viktorzettel/Downloads/ViktorAI/MM
python3 deployment/vps/env_fingerprint.py --env-file .env
```

On VPS:

```bash
cd ~/kou-bot
.venv/bin/python deployment/vps/env_fingerprint.py --env-file .env
```

The five fingerprints and lengths should match for `POLY_PRIVATE_KEY`, `POLY_PROXY_ADDRESS`, `POLY_API_KEY`, `POLY_API_SECRET`, and `POLY_API_PASSPHRASE`.

## Sanity Test

```bash
cd ~/kou-bot
.venv/bin/python -m py_compile kou_dual_compact_web.py kou_dual_compact_monitor.py kou_polymarket_live_capture.py polymarket_token_sniper.py
.venv/bin/python -m pytest tests/test_polymarket_token_sniper.py tests/test_kou_polymarket_live_capture.py
```

Expected: `34 passed`.

## Start 4h Dry-Run

Detached, recommended:

```bash
cd ~/kou-bot
nohup bash deployment/vps/run_4h_dry_run.sh > vps_run.out 2>&1 &
echo $! > vps_run.pid
```

Foreground, only if the Mac/SSH tab will stay open:

```bash
cd ~/kou-bot
bash deployment/vps/run_4h_dry_run.sh
```

This is dry-run only. It should not send live orders.

## Start Tiny Supervised Live Test

Only use this when the user explicitly confirms they are ready to spend real pUSD and will supervise the run. This uses `1 pUSD/order`, `4 pUSD/session`, `4 orders/session`, `FOK` only.

```bash
cd ~/kou-bot
KOU_I_UNDERSTAND_REAL_MONEY=YES nohup bash deployment/vps/run_4h_live_tiny.sh > vps_live_run.out 2>&1 &
echo $! > vps_live_run.pid
```

The run still captures quotes, markets, grid signals, shadow orders, sniper signals, sniper plans, live results, live ledger, and logs for v3/v4 analysis.

## Monitor Health

Current session:

```bash
cd ~/kou-bot
SESSION_ID=$(cat .kou_current_session)
echo "$SESSION_ID"
```

Summary:

```bash
cd ~/kou-bot
SESSION_ID=$(cat .kou_current_session)
.venv/bin/python deployment/vps/summarize_session.py data/live_capture/$SESSION_ID
```

Good signs:

- `quote_rows` increasing
- `sources: ['browser-poly-chainlink']`
- `source_age` mean and p95 under a few seconds
- `source_age_gt_10s: 0` or near zero
- `quote_latency` mean under roughly `0.2s`
- `alignments` mostly `aligned`

Logs:

```bash
cd ~/kou-bot
SESSION_ID=$(cat .kou_current_session)
tail -40 data/live_capture/$SESSION_ID/web.log
tail -40 data/live_capture/$SESSION_ID/headless_browser.log
tail -40 data/live_capture/$SESSION_ID/polymarket_capture.log
```

Live tail:

```bash
tail -f ~/kou-bot/vps_run.out
```

## Stop Current Run

```bash
cd ~/kou-bot
SESSION_ID=$(cat .kou_current_session)
kill $(cat data/live_capture/$SESSION_ID/capture.pid) 2>/dev/null || true
kill $(cat data/live_capture/$SESSION_ID/browser.pid) 2>/dev/null || true
kill $(cat data/live_capture/$SESSION_ID/web.pid) 2>/dev/null || true
kill $(cat vps_run.pid) 2>/dev/null || true
```

## Fetch Results To Mac

On Mac:

```bash
cd /Users/viktorzettel/Downloads/ViktorAI/MM
scp -i poly-bot-key.pem -r ubuntu@52.215.220.165:~/kou-bot/data/live_capture/SESSION_ID data/live_capture/
```

Then analyze the copied session locally.

## Common Failure Checks

If browser log shows `source_age_s=None`, first run the summary. The helper log may be wrong or early; the summary is authoritative.

If `web.log` says `ModuleNotFoundError: kou_dual_compact_monitor`, copy the missing file:

```bash
cd /Users/viktorzettel/Downloads/ViktorAI/MM
scp -i poly-bot-key.pem kou_dual_compact_monitor.py ubuntu@52.215.220.165:~/kou-bot/
```

If CLOB quote rows are zero, inspect:

```bash
cd ~/kou-bot
SESSION_ID=$(cat .kou_current_session)
tail -80 data/live_capture/$SESSION_ID/web.log
tail -80 data/live_capture/$SESSION_ID/polymarket_capture.log
ss -ltnp | grep 8071 || true
```
