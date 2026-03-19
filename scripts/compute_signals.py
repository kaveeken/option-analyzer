#!/usr/bin/env python3
"""
Daily IV/RV spike-trough signal computation.

Reads collected snapshots and computes rolling z-score and percentile-rank
signals for implied volatility (iv_30d) and realized/historical volatility
(hist_vol) per symbol.

Run after collect_snapshots.py — either manually or via a systemd timer.

Usage:
    python scripts/compute_signals.py
    python scripts/compute_signals.py --lookback 60 --spike-threshold 1.5
    python scripts/compute_signals.py --db /path/to/snapshots.db --dry-run
"""

import argparse
import logging
import sqlite3
import statistics
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "snapshots.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS volatility_signals (
    date            TEXT NOT NULL,
    symbol          TEXT NOT NULL,

    -- raw values (copied from snapshot for convenience)
    iv_30d          REAL,
    hist_vol        REAL,

    -- z-scores: (today - rolling_mean) / rolling_std
    iv_zscore       REAL,
    rv_zscore       REAL,

    -- percentile rank within the lookback window (0–100)
    iv_pct_rank     REAL,
    rv_pct_rank     REAL,

    -- classification: spike | elevated | neutral | depressed | trough
    iv_signal       TEXT,
    rv_signal       TEXT,

    -- how many historical data points were available
    iv_lookback_n   INTEGER,
    rv_lookback_n   INTEGER,

    computed_at     TEXT DEFAULT (datetime('now')),

    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_date
    ON volatility_signals (symbol, date);
CREATE INDEX IF NOT EXISTS idx_signals_date
    ON volatility_signals (date);
"""

# Minimum number of historical points required to emit a signal.
MIN_HISTORY = 10


def _zscore(value: float, history: list[float]) -> float | None:
    """Z-score of value relative to history (history does NOT include today)."""
    if len(history) < MIN_HISTORY:
        return None
    mean = statistics.mean(history)
    try:
        std = statistics.stdev(history)
    except statistics.StatisticsError:
        return None
    if std == 0:
        return 0.0
    return (value - mean) / std


def _pct_rank(value: float, history: list[float]) -> float | None:
    """Percentile rank of value within history (0–100, inclusive of today)."""
    if len(history) < MIN_HISTORY:
        return None
    pool = history + [value]
    n_below = sum(1 for x in pool if x < value)
    # Mid-point rank (handles ties naturally)
    n_equal = sum(1 for x in pool if x == value)
    rank = (n_below + 0.5 * n_equal) / len(pool) * 100
    return round(rank, 1)


def _classify(zscore: float | None, spike_threshold: float) -> str | None:
    """Map z-score to a human-readable signal label."""
    if zscore is None:
        return None
    if zscore >= spike_threshold * 2:
        return "spike"
    if zscore >= spike_threshold:
        return "elevated"
    if zscore <= -spike_threshold * 2:
        return "trough"
    if zscore <= -spike_threshold:
        return "depressed"
    return "neutral"


def compute_signals(
    conn: sqlite3.Connection,
    lookback: int,
    spike_threshold: float,
    dry_run: bool,
) -> int:
    """
    Compute IV/RV signals for every symbol present in today's snapshot.

    Looks back up to `lookback` prior trading days (not including today).
    Returns the number of rows produced.
    """
    target_str = date.today().isoformat()

    # Fetch all symbols that have data for target_date
    rows = conn.execute(
        "SELECT symbol, iv_30d, hist_vol FROM stock_snapshots WHERE date = ?",
        (target_str,),
    ).fetchall()

    if not rows:
        logger.warning(f"No snapshot data found for {target_str}")
        return 0

    logger.info(f"Computing signals for {len(rows)} symbols on {target_str} "
                f"(lookback={lookback}, spike_threshold=±{spike_threshold}σ)")

    signal_rows = []
    for symbol, iv_today, rv_today in rows:
        # Fetch historical values (excluding target_date, most-recent-first)
        hist_rows = conn.execute(
            """
            SELECT iv_30d, hist_vol
            FROM stock_snapshots
            WHERE symbol = ? AND date < ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (symbol, target_str, lookback),
        ).fetchall()

        iv_history = [r[0] for r in hist_rows if r[0] is not None]
        rv_history = [r[1] for r in hist_rows if r[1] is not None]

        # IV signal
        if iv_today is not None and len(iv_history) >= MIN_HISTORY:
            iv_z = _zscore(iv_today, iv_history)
            iv_p = _pct_rank(iv_today, iv_history)
            iv_sig = _classify(iv_z, spike_threshold)
        else:
            iv_z = iv_p = iv_sig = None

        # RV signal
        if rv_today is not None and len(rv_history) >= MIN_HISTORY:
            rv_z = _zscore(rv_today, rv_history)
            rv_p = _pct_rank(rv_today, rv_history)
            rv_sig = _classify(rv_z, spike_threshold)
        else:
            rv_z = rv_p = rv_sig = None

        signal_rows.append((
            target_str,
            symbol,
            iv_today,
            rv_today,
            round(iv_z, 4) if iv_z is not None else None,
            round(rv_z, 4) if rv_z is not None else None,
            iv_p,
            rv_p,
            iv_sig,
            rv_sig,
            len(iv_history),
            len(rv_history),
        ))

        if iv_sig in ("spike", "trough") or rv_sig in ("spike", "trough"):
            logger.info(
                f"  {symbol:8s}  IV={iv_today} ({iv_sig}, z={iv_z:.2f if iv_z else 'n/a'})  "
                f"RV={rv_today} ({rv_sig}, z={rv_z:.2f if rv_z else 'n/a'})"
            )

    if dry_run:
        spikes = sum(
            1 for r in signal_rows
            if r[8] in ("spike", "trough") or r[9] in ("spike", "trough")
        )
        logger.info(f"[DRY RUN] Would write {len(signal_rows)} rows; {spikes} spike/trough signals")
        return len(signal_rows)

    conn.executemany(
        """
        INSERT OR REPLACE INTO volatility_signals
            (date, symbol, iv_30d, hist_vol,
             iv_zscore, rv_zscore,
             iv_pct_rank, rv_pct_rank,
             iv_signal, rv_signal,
             iv_lookback_n, rv_lookback_n)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        signal_rows,
    )
    conn.commit()

    # Summary log
    by_signal = {}
    for r in signal_rows:
        for sig in (r[8], r[9]):
            if sig:
                by_signal[sig] = by_signal.get(sig, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_signal.items()))
    logger.info(f"Wrote {len(signal_rows)} signal rows. Distribution: {summary or 'all null'}")

    return len(signal_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute daily IV/RV spike-trough signals from collected snapshots"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        metavar="PATH",
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=60,
        metavar="N",
        help="Number of prior trading days to use as the rolling window (default: 60)",
    )
    parser.add_argument(
        "--spike-threshold",
        type=float,
        default=1.5,
        metavar="SIGMA",
        help=(
            "Z-score threshold for signal classification (default: 1.5). "
            "  |z| >= threshold*2 → spike/trough; "
            "  |z| >= threshold   → elevated/depressed"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute signals but do not write to DB",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not args.db.exists():
        logger.error(f"Database not found: {args.db}")
        raise SystemExit(1)

    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)
    conn.commit()

    try:
        n = compute_signals(
            conn=conn,
            lookback=args.lookback,
            spike_threshold=args.spike_threshold,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    raise SystemExit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
