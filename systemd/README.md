# Systemd Setup

Install the web app (user service) and the daily snapshot collector
(user or system service).

## Web app (user service)

`option-analyzer-app.service` runs the FastAPI app. It is installed as a
**user service** under `pi`, not a system service.

```bash
mkdir -p ~/.config/systemd/user/
cp systemd/option-analyzer-app.service ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now option-analyzer-app.service
systemctl --user status option-analyzer-app.service
```

The unit reads `/home/pi/proj/option-analyzer/.env` (optional) and
appends stdout/stderr to `app.log` in the project directory.

# Snapshot collector

## Install (system-level)

```bash
# Copy units
sudo cp systemd/option-analyzer-collect.service /etc/systemd/system/
sudo cp systemd/option-analyzer-collect.timer   /etc/systemd/system/

# Edit paths if needed
sudo systemctl daemon-reload

# Enable and start the timer
sudo systemctl enable --now option-analyzer-collect.timer

# Check status
systemctl status option-analyzer-collect.timer
systemctl list-timers option-analyzer-collect.timer
```

## Install (user-level, no sudo)

```bash
mkdir -p ~/.config/systemd/user/
cp systemd/option-analyzer-collect.service ~/.config/systemd/user/
cp systemd/option-analyzer-collect.timer   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now option-analyzer-collect.timer
systemctl --user list-timers
```

## Run manually

```bash
# Dry run (no DB writes)
python scripts/collect_snapshots.py --dry-run

# Compute signals only (e.g. to re-run with different parameters)
python scripts/compute_signals.py --lookback 60 --spike-threshold 1.5

# Run the systemd service immediately (no wait for timer)
sudo systemctl start option-analyzer-collect.service

# Watch logs
journalctl -fu option-analyzer-collect.service
```

## Schedule

Fires Mon–Fri at **22:00 UTC** (18:00 ET · 23:00 CET).
`Persistent=true` means it will catch up on missed runs after downtime.

## Symbol lists

Add/edit `.txt` files in `scripts/symbols/`. One symbol per line. Lines
starting with `#` are comments. All files are merged and deduplicated.

Conids are cached in `data/conid_cache.json` after the first successful
resolution — IBKR symbol lookups only happen once per symbol.

## Signal computation

After each collection, `compute_signals.py` reads the snapshot DB and writes
a `volatility_signals` table with per-symbol z-scores and percentile ranks for
IV (`iv_30d`) and realized vol (`hist_vol`).

Signal labels: **spike** (z ≥ 3σ) · **elevated** (z ≥ 1.5σ) · **neutral** ·
**depressed** (z ≤ −1.5σ) · **trough** (z ≤ −3σ).

Thresholds and lookback window are configurable:

```bash
python scripts/compute_signals.py --lookback 60 --spike-threshold 1.5
```

Query spikes across all symbols on a date:

```sql
SELECT symbol, iv_30d, iv_zscore, iv_signal, hist_vol, rv_zscore, rv_signal
FROM volatility_signals
WHERE date = '2026-03-18'
  AND (iv_signal IN ('spike','trough') OR rv_signal IN ('spike','trough'))
ORDER BY abs(iv_zscore) DESC;
```

# Pi deploy prerequisites (CI)

The `deploy` job in `.github/workflows/ci.yml` runs on a self-hosted
runner on the Pi. A fresh Pi needs the following one-time setup before
CI can deploy:

## 1. Stable clone at `/home/pi/proj/option-analyzer`

```bash
mkdir -p /home/pi/proj
git clone ssh://git@github.com/kaveeken/option-analyzer.git /home/pi/proj/option-analyzer
```

CI pulls into this path in place (no `actions/checkout`) so the service
always runs from a stable, git-tracked location. This also lets the
`/health` endpoint report the current commit hash.

## 2. SSH deploy key

The workflow sets `GIT_SSH_COMMAND` to use a key at
`/home/pi/.ssh/github_ed25519`. Generate it **passphraseless** and add
the public key to GitHub (as a repo deploy key or on an account with
access):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_ed25519 -N '' -C 'pi@rasp deploy'
cat ~/.ssh/github_ed25519.pub   # paste to GitHub
```

The key must be passphraseless — the runner's systemd service has no
SSH agent, so anything interactive will hang.

## 3. Enable lingering for `pi`

```bash
sudo loginctl enable-linger pi
loginctl show-user pi | grep Linger   # should say Linger=yes
```

Without this, `/run/user/1000` (where pi's user bus lives) only exists
while pi is interactively logged in, and `systemctl --user` from CI
would fail.

## 4. GitHub Actions runner as `pi`

Install the self-hosted runner following GitHub's docs, then configure
the service to run as `pi`:

```bash
cd ~/actions-runner
sudo ./svc.sh install pi
sudo ./svc.sh start
```

Verify:

```bash
systemctl status 'actions.runner.*'
grep ^User= /etc/systemd/system/actions.runner.*.service   # User=pi
```

Running as `pi` is load-bearing: the workflow assumes it can talk to
pi's user bus directly (via `XDG_RUNTIME_DIR=/run/user/1000`) and that
git pulls write as the owning user of the working tree.

## 5. `.env` file

Create `/home/pi/proj/option-analyzer/.env` with any secrets/config the
app needs. The service unit uses `EnvironmentFile=-...` (optional
prefix) so the app will still start if the file is missing, but most
features won't work without it.
