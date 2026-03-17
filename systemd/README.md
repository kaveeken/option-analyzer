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

# Collect for a specific past date
python scripts/collect_snapshots.py --date 2026-03-14

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
