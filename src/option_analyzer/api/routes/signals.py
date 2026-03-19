"""
Volatility signals endpoint.

Reads pre-computed spike/trough signals from the local snapshots database.
No IBKR connection required — signals must already be computed by
scripts/compute_signals.py.
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/signals", tags=["signals"])

_DB_PATH = Path("data/snapshots.db")

SIGNAL_LEVELS = {"spike", "elevated", "neutral", "depressed", "trough"}


class SignalRow(BaseModel):
    symbol: str
    date: str
    iv_30d: float | None
    hist_vol: float | None
    price_rv: float | None
    iv_zscore: float | None
    rv_zscore: float | None
    price_rv_zscore: float | None
    iv_pct_rank: float | None
    rv_pct_rank: float | None
    price_rv_pct_rank: float | None
    iv_signal: str | None
    rv_signal: str | None
    price_rv_signal: str | None
    iv_lookback_n: int | None
    rv_lookback_n: int | None
    price_rv_lookback_n: int | None
    # Derived: max absolute z-score across all three signals
    max_abs_zscore: float | None


class SignalsResponse(BaseModel):
    data_date: str | None
    age_days: int | None
    signals: list[SignalRow]


def _max_abs(a: float | None, b: float | None, c: float | None) -> float | None:
    values = [v for v in (a, b, c) if v is not None]
    return max(abs(v) for v in values) if values else None


@router.get("", response_model=SignalsResponse)
async def get_signals(
    signal: str | None = Query(
        default=None,
        description=(
            "Comma-separated signal levels to include "
            "(spike, elevated, neutral, depressed, trough). "
            "Matches any of iv_signal, rv_signal, price_rv_signal. "
            "Omit to return all."
        ),
    ),
) -> SignalsResponse:
    """
    Return the latest volatility signals for all symbols.

    Signals are filtered to the most recent date present in the database.
    The response includes an age_days field so the frontend can show a
    freshness warning when data is stale.
    """
    if not _DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Signals database not found. Run scripts/compute_signals.py first.",
        )

    # Parse signal filter
    requested_levels: set[str] | None = None
    if signal:
        requested_levels = {s.strip().lower() for s in signal.split(",")}
        unknown = requested_levels - SIGNAL_LEVELS
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown signal level(s): {unknown}. Valid: {SIGNAL_LEVELS}",
            )

    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Find latest date with signal data
        row = conn.execute("SELECT MAX(date) FROM volatility_signals").fetchone()
        if not row or not row[0]:
            return SignalsResponse(data_date=None, age_days=None, signals=[])

        data_date: str = row[0]
        age_days = (date.today() - date.fromisoformat(data_date)).days

        rows = conn.execute(
            """
            SELECT symbol, date,
                   iv_30d, hist_vol, price_rv,
                   iv_zscore, rv_zscore, price_rv_zscore,
                   iv_pct_rank, rv_pct_rank, price_rv_pct_rank,
                   iv_signal, rv_signal, price_rv_signal,
                   iv_lookback_n, rv_lookback_n, price_rv_lookback_n
            FROM volatility_signals
            WHERE date = ?
            ORDER BY symbol
            """,
            (data_date,),
        ).fetchall()

    finally:
        conn.close()

    signals: list[SignalRow] = []
    for r in rows:
        d: dict[str, Any] = dict(r)
        iv_sig = d.get("iv_signal")
        rv_sig = d.get("rv_signal")
        prv_sig = d.get("price_rv_signal")

        # Apply signal level filter: include row if any of the three signals match
        if requested_levels is not None:
            if not ({iv_sig, rv_sig, prv_sig} & requested_levels):
                continue

        signals.append(SignalRow(
            **d,
            max_abs_zscore=_max_abs(
                d.get("iv_zscore"),
                d.get("rv_zscore"),
                d.get("price_rv_zscore"),
            ),
        ))

    # Sort by max abs z-score descending (None last)
    signals.sort(key=lambda s: s.max_abs_zscore or 0.0, reverse=True)

    return SignalsResponse(data_date=data_date, age_days=age_days, signals=signals)
