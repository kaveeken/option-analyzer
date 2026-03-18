#!/usr/bin/env python3
"""
Daily snapshot collector for stock market data.

Collects OHLC + IV/dividend data for configured symbol lists and stores
in a local SQLite database. Designed to run nightly via systemd timer,
after both US and EU market close.

Usage:
    python scripts/collect_snapshots.py
    python scripts/collect_snapshots.py --dry-run
    python scripts/collect_snapshots.py --date 2026-03-14
    python scripts/collect_snapshots.py --db /path/to/snapshots.db --log-level DEBUG
"""

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Resolve src/ on the path so we can import from the main package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from option_analyzer.clients.cache import InMemoryCache
from option_analyzer.clients.ibkr import IBKRClient
from option_analyzer.config import Settings
from option_analyzer.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "snapshots.db"
DEFAULT_CACHE = PROJECT_ROOT / "data" / "conid_cache.json"
DEFAULT_SYMBOLS_DIR = Path(__file__).parent / "symbols"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_snapshots (
    date                TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    conid               INTEGER NOT NULL,
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    volume              REAL,
    iv_30d              REAL,
    hist_vol            REAL,
    iv_hv_ratio         REAL,
    dividends_forward   REAL,
    dividends_ttm       REAL,
    collected_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_date ON stock_snapshots (symbol, date);
"""

# Fields: price + vol metrics + dividends (same as IBKRClient._STOCK_FIELDS)
_SNAPSHOT_FIELDS = "31,84,86,7283,7087,7084,7671,7672"
_BATCH_SIZE = 9  # conids per market snapshot request
_TICKLE_INTERVAL = 30  # seconds between keepalive pings


async def check_auth(ibkr: IBKRClient) -> None:
    """
    Verify the IBKR portal session is authenticated.

    Raises SystemExit if not authenticated — the collector cannot
    re-authenticate (that requires a browser login).
    """
    try:
        status = await ibkr.get_request("iserver/auth/status")
        authenticated = status.get("authenticated", False)
        competing = status.get("competing", False)
    except Exception as e:
        logger.error(f"Could not reach IBKR portal: {e}")
        raise SystemExit(1)

    if competing:
        logger.warning("IBKR portal reports a competing session — data may be unreliable")
    if not authenticated:
        logger.error(
            "IBKR portal is not authenticated. "
            "Log in via the Client Portal web UI, then retry."
        )
        raise SystemExit(1)

    logger.info("IBKR portal authenticated OK")


async def tickle_loop(ibkr: IBKRClient) -> None:
    """
    Background task: ping /tickle every _TICKLE_INTERVAL seconds to prevent
    the IBKR portal session from timing out during a long collection run.
    """
    while True:
        await asyncio.sleep(_TICKLE_INTERVAL)
        try:
            await ibkr.get_request("tickle")
            logger.debug("Tickle OK")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Tickle failed: {e}")


def load_symbols(symbols_dir: Path) -> list[str]:
    """Load all symbols from .txt files in the symbols directory (deduplicated)."""
    symbols: set[str] = set()
    for f in sorted(symbols_dir.glob("*.txt")):
        for raw_line in f.read_text().splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#"):
                symbols.add(line.upper())
    result = sorted(symbols)
    logger.info(f"Loaded {len(result)} unique symbols from {symbols_dir}")
    return result


def load_conid_cache(cache_path: Path) -> dict[str, int]:
    if cache_path.exists():
        data = json.loads(cache_path.read_text())
        logger.info(f"Loaded {len(data)} cached conids from {cache_path}")
        return data
    return {}


def save_conid_cache(cache_path: Path, cache: dict[str, int]) -> None:
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


async def resolve_conids(
    ibkr: IBKRClient,
    symbols: list[str],
    conid_cache: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """
    Resolve symbols to IBKR conids, using cache for already-known symbols.

    Returns:
        (updated_cache, failed_symbols)
    """
    missing = [s for s in symbols if s not in conid_cache]
    failed: list[str] = []

    if missing:
        logger.info(f"Resolving {len(missing)} new conids (not in cache)...")
        for symbol in missing:
            try:
                conid = await ibkr.get_conid(symbol)
                conid_cache[symbol] = conid
                logger.debug(f"  {symbol} -> conid {conid}")
            except Exception as e:
                logger.warning(f"  Could not resolve {symbol}: {e}")
                failed.append(symbol)

    return conid_cache, failed


async def fetch_iv_snapshots(
    ibkr: IBKRClient,
    conids: list[int],
) -> dict[int, dict[str, Any]]:
    """Batch-fetch IV and dividend fields for all conids."""
    results: dict[int, dict[str, Any]] = {}

    for i in range(0, len(conids), _BATCH_SIZE):
        batch = conids[i : i + _BATCH_SIZE]
        try:
            snapshots = await ibkr.get_market_snapshot(
                conid=",".join(str(c) for c in batch),
                ttl=None,  # no caching — we want fresh data each run
                fields=_SNAPSHOT_FIELDS,
            )
            for snap in snapshots:
                conid = snap.get("conid")
                if conid is not None:
                    results[conid] = snap
        except Exception as e:
            logger.warning(f"IV snapshot batch failed (conids {batch[:3]}...): {e}")

    logger.info(f"Fetched IV snapshots for {len(results)}/{len(conids)} conids")
    return results


async def fetch_ohlc(ibkr: IBKRClient, conid: int) -> dict[str, float | None] | None:
    """Fetch the most recent daily OHLC bar via 5-day history endpoint."""
    try:
        endpoint = f"iserver/marketdata/history?conid={conid}&period=5d&bar=1d"
        response = await ibkr.get_request(endpoint)
        if not isinstance(response, dict) or "data" not in response:
            return None
        bars = response["data"]
        if not bars:
            return None
        bar = bars[-1]  # most recent trading day
        return {
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
        }
    except Exception as e:
        logger.warning(f"OHLC fetch failed for conid {conid}: {e}")
        return None


async def fetch_all_ohlc(
    ibkr: IBKRClient,
    resolved: dict[str, int],
) -> dict[str, dict[str, float | None] | None]:
    """Concurrently fetch OHLC for all resolved symbols."""
    logger.info(f"Fetching OHLC for {len(resolved)} symbols...")

    async def _fetch(symbol: str, conid: int) -> tuple[str, dict | None]:
        return symbol, await fetch_ohlc(ibkr, conid)

    tasks = [_fetch(s, c) for s, c in resolved.items()]
    pairs = await asyncio.gather(*tasks)
    return dict(pairs)


async def collect(
    db_path: Path,
    cache_path: Path,
    symbols_dir: Path,
    today: date,
    dry_run: bool = False,
) -> int:
    """
    Run one collection cycle.

    Returns:
        Number of rows written (0 on dry-run).
    """
    symbols = load_symbols(symbols_dir)
    if not symbols:
        logger.error(f"No symbols found in {symbols_dir}")
        return 0

    conid_cache = load_conid_cache(cache_path)

    settings = Settings()
    ibkr_cache = InMemoryCache()
    rate_limiter = RateLimiter(max_requests=10, per_seconds=1)

    async with IBKRClient(settings, ibkr_cache, rate_limiter) as ibkr:
        # Verify portal is up and authenticated before doing any real work
        await check_auth(ibkr)

        # Keep the portal session alive for the duration of the collection run
        tickle_task = asyncio.create_task(tickle_loop(ibkr))

        try:
            # Phase 1: Resolve conids
            conid_cache, failed = await resolve_conids(ibkr, symbols, conid_cache)
            if failed:
                logger.warning(f"Failed to resolve {len(failed)} symbols: {failed}")

            if not dry_run:
                save_conid_cache(cache_path, conid_cache)

            resolved = {s: conid_cache[s] for s in symbols if s in conid_cache}
            logger.info(f"Proceeding with {len(resolved)}/{len(symbols)} resolved symbols")

            # Phase 2: Batch IV/dividend snapshots
            conids = list(resolved.values())
            iv_data = await fetch_iv_snapshots(ibkr, conids)

            # Phase 3: Concurrent OHLC fetches
            ohlc_data = await fetch_all_ohlc(ibkr, resolved)
        finally:
            tickle_task.cancel()
            try:
                await tickle_task
            except asyncio.CancelledError:
                pass

    # Phase 4: Build rows
    today_str = today.isoformat()
    rows = []
    for symbol, conid in resolved.items():
        ohlc = ohlc_data.get(symbol)
        iv = iv_data.get(conid, {})
        rows.append((
            today_str,
            symbol,
            conid,
            ohlc.get("open") if ohlc else None,
            ohlc.get("high") if ohlc else None,
            ohlc.get("low") if ohlc else None,
            ohlc.get("close") if ohlc else None,
            ohlc.get("volume") if ohlc else None,
            iv.get("iv_30d"),
            iv.get("hist_vol"),
            iv.get("iv_hv_ratio"),
            iv.get("dividends_forward"),
            iv.get("dividends_ttm"),
        ))

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} rows for {today_str}")
        for r in rows[:5]:
            logger.info(f"  sample: {r[:4]}...")
        return 0

    # Phase 5: Write to SQLite
    conn = init_db(db_path)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO stock_snapshots
                (date, symbol, conid, open, high, low, close, volume,
                 iv_30d, hist_vol, iv_hv_ratio, dividends_forward, dividends_ttm)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Wrote {len(rows)} rows to {db_path} for {today_str}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect daily OHLC + IV/dividend snapshots for configured stock lists"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        metavar="PATH",
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        metavar="PATH",
        help=f"Conid cache JSON path (default: {DEFAULT_CACHE})",
    )
    parser.add_argument(
        "--symbols",
        type=Path,
        default=DEFAULT_SYMBOLS_DIR,
        metavar="DIR",
        help=f"Directory containing symbol list .txt files (default: {DEFAULT_SYMBOLS_DIR})",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today(),
        metavar="YYYY-MM-DD",
        help="Collection date (default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve symbols and fetch data but do not write to DB",
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

    n = asyncio.run(
        collect(
            db_path=args.db,
            cache_path=args.cache,
            symbols_dir=args.symbols,
            today=args.date,
            dry_run=args.dry_run,
        )
    )
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
