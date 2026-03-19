#!/usr/bin/env python3
"""
Daily IV/RV spike-trough signal computation.

Reads collected snapshots and computes rolling z-score and percentile-rank
signals for:
  - iv_30d   — implied volatility from IBKR field 7283
  - hist_vol — historical vol from IBKR field 7087
  - price_rv — realized vol computed from close-price log returns

Run after collect_snapshots.py — either manually or via a systemd timer.

Usage:
    python scripts/compute_signals.py
    python scripts/compute_signals.py --lookback 60 --rv-window 20 --spike-threshold 1.5
    python scripts/compute_signals.py --db /path/to/snapshots.db --dry-run
"""

import argparse
import logging
import math
import sqlite3
import statistics
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "snapshots.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS volatility_signals (
    date                TEXT NOT NULL,
    symbol              TEXT NOT NULL,

    -- raw values (copied from snapshot for convenience)
    iv_30d              REAL,
    hist_vol            REAL,

    -- realized vol computed from close-price log returns (annualized %)
    price_rv            REAL,

    -- z-scores: (today - rolling_mean) / rolling_std
    iv_zscore           REAL,
    rv_zscore           REAL,
    price_rv_zscore     REAL,

    -- percentile rank within the lookback window (0–100)
    iv_pct_rank         REAL,
    rv_pct_rank         REAL,
    price_rv_pct_rank   REAL,

    -- classification: spike | elevated | neutral | depressed | trough
    iv_signal           TEXT,
    rv_signal           TEXT,
    price_rv_signal     TEXT,

    -- how many historical data points were available
    iv_lookback_n       INTEGER,
    rv_lookback_n       INTEGER,
    price_rv_lookback_n INTEGER,

    computed_at         TEXT DEFAULT (datetime('now')),

    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_date
    ON volatility_signals (symbol, date);
CREATE INDEX IF NOT EXISTS idx_signals_date
    ON volatility_signals (date);
"""

SCHEMA_MIGRATIONS = [
    # Add price_rv columns to tables created before this feature was added.
    "ALTER TABLE volatility_signals ADD COLUMN price_rv            REAL",
    "ALTER TABLE volatility_signals ADD COLUMN price_rv_zscore     REAL",
    "ALTER TABLE volatility_signals ADD COLUMN price_rv_pct_rank   REAL",
    "ALTER TABLE volatility_signals ADD COLUMN price_rv_signal     TEXT",
    "ALTER TABLE volatility_signals ADD COLUMN price_rv_lookback_n INTEGER",
]

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


def _rolling_rv(closes: list[float], window: int) -> list[float | None]:
    """
    Compute annualized realized volatility for each position in closes.

    Uses `window` log returns ending at each position (requires window+1 prices).
    Returns a parallel list; entries with insufficient history are None.
    RV is expressed as an annualized percentage (std * sqrt(252) * 100).
    """
    result: list[float | None] = []
    for i in range(len(closes)):
        if i < window:
            result.append(None)
            continue
        segment = closes[i - window: i + 1]  # window+1 prices → window returns
        log_returns = [math.log(segment[j] / segment[j - 1]) for j in range(1, len(segment))]
        try:
            rv = statistics.stdev(log_returns) * math.sqrt(252) * 100
        except statistics.StatisticsError:
            rv = None
        result.append(rv)
    return result


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
    rv_window: int,
    spike_threshold: float,
    dry_run: bool,
) -> int:
    """
    Compute IV/RV signals for every symbol present in today's snapshot.

    Looks back up to `lookback` prior trading days (not including today).
    For price_rv, fetches lookback + rv_window prior closes to build a full
    rolling RV history for z-scoring.
    Returns the number of rows produced.
    """
    target_str = date.today().isoformat()

    rows = conn.execute(
        "SELECT symbol, iv_30d, hist_vol, close FROM stock_snapshots WHERE date = ?",
        (target_str,),
    ).fetchall()

    if not rows:
        logger.warning(f"No snapshot data found for {target_str}")
        return 0

    logger.info(
        f"Computing signals for {len(rows)} symbols on {target_str} "
        f"(lookback={lookback}, rv_window={rv_window}, spike_threshold=±{spike_threshold}σ)"
    )

    signal_rows = []
    for symbol, iv_today, rv_today, close_today in rows:
        # --- IBKR iv_30d and hist_vol signals ---
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

        if iv_today is not None and len(iv_history) >= MIN_HISTORY:
            iv_z = _zscore(iv_today, iv_history)
            iv_p = _pct_rank(iv_today, iv_history)
            iv_sig = _classify(iv_z, spike_threshold)
        else:
            iv_z = iv_p = iv_sig = None

        if rv_today is not None and len(rv_history) >= MIN_HISTORY:
            rv_z = _zscore(rv_today, rv_history)
            rv_p = _pct_rank(rv_today, rv_history)
            rv_sig = _classify(rv_z, spike_threshold)
        else:
            rv_z = rv_p = rv_sig = None

        # --- price_rv: realized vol from close-price log returns ---
        # Need lookback prior RV values (each of which requires rv_window closes
        # before it), plus today's close.  Total closes needed:
        #   today + lookback prior RV values + rv_window lead-in = lookback + rv_window + 1
        # We fetch lookback + rv_window prior closes (ascending) then append today's.
        close_rows = conn.execute(
            """
            SELECT close FROM stock_snapshots
            WHERE symbol = ? AND date < ? AND close IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
            """,
            (symbol, target_str, lookback + rv_window),
        ).fetchall()

        prior_closes = [r[0] for r in reversed(close_rows)]  # oldest → newest

        price_rv_z = price_rv_p = price_rv_sig = price_rv_today = None
        price_rv_lookback_n = 0

        if close_today is not None and len(prior_closes) >= rv_window:
            all_closes = prior_closes + [close_today]
            rv_series = _rolling_rv(all_closes, rv_window)

            price_rv_today = rv_series[-1]  # RV ending on today's close
            # History for z-scoring: all computed RVs except today's
            price_rv_history = [v for v in rv_series[:-1] if v is not None]
            price_rv_lookback_n = len(price_rv_history)

            if price_rv_today is not None and price_rv_lookback_n >= MIN_HISTORY:
                price_rv_z = _zscore(price_rv_today, price_rv_history)
                price_rv_p = _pct_rank(price_rv_today, price_rv_history)
                price_rv_sig = _classify(price_rv_z, spike_threshold)

        signal_rows.append((
            target_str,
            symbol,
            iv_today,
            rv_today,
            round(price_rv_today, 4) if price_rv_today is not None else None,
            round(iv_z, 4) if iv_z is not None else None,
            round(rv_z, 4) if rv_z is not None else None,
            round(price_rv_z, 4) if price_rv_z is not None else None,
            iv_p,
            rv_p,
            price_rv_p,
            iv_sig,
            rv_sig,
            price_rv_sig,
            len(iv_history),
            len(rv_history),
            price_rv_lookback_n,
        ))

        if any(s in ("spike", "trough") for s in (iv_sig, rv_sig, price_rv_sig)):
            logger.info(
                f"  {symbol:8s}  "
                f"IV={iv_today} ({iv_sig})  "
                f"RV(ibkr)={rv_today} ({rv_sig})  "
                f"RV(price)={price_rv_today:.1f if price_rv_today else 'n/a'} ({price_rv_sig})"
            )

    if dry_run:
        spikes = sum(
            1 for r in signal_rows
            if any(r[i] in ("spike", "trough") for i in (11, 12, 13))
        )
        logger.info(f"[DRY RUN] Would write {len(signal_rows)} rows; {spikes} spike/trough signals")
        return len(signal_rows)

    conn.executemany(
        """
        INSERT OR REPLACE INTO volatility_signals
            (date, symbol, iv_30d, hist_vol, price_rv,
             iv_zscore, rv_zscore, price_rv_zscore,
             iv_pct_rank, rv_pct_rank, price_rv_pct_rank,
             iv_signal, rv_signal, price_rv_signal,
             iv_lookback_n, rv_lookback_n, price_rv_lookback_n)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        signal_rows,
    )
    conn.commit()

    by_signal: dict[str, int] = {}
    for r in signal_rows:
        for sig in (r[11], r[12], r[13]):
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
        help="Number of prior trading days to use as the z-score rolling window (default: 60)",
    )
    parser.add_argument(
        "--rv-window",
        type=int,
        default=20,
        metavar="N",
        help="Number of trading days of log returns used to compute price_rv (default: 20)",
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
    for migration in SCHEMA_MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()

    try:
        n = compute_signals(
            conn=conn,
            lookback=args.lookback,
            rv_window=args.rv_window,
            spike_threshold=args.spike_threshold,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    raise SystemExit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
