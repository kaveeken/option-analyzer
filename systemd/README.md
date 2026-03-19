# Systemd Timer Setup

Install the daily snapshot collector as a systemd user or system service.

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
