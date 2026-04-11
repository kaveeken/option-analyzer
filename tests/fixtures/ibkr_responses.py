"""
Shared IBKR test fixtures and factory functions.

Provides canonical test data for Stock, OptionChain, and OptionContract
objects across all test layers (unit, integration, e2e).
"""

from datetime import date, timedelta
from typing import Literal

import numpy as np

from option_analyzer.models.domain import OptionChain, OptionContract, Stock


# ============================================================================
# Stock Fixtures
# ============================================================================

def make_stock(
    symbol: str = "AAPL",
    current_price: float = 150.0,
    conid: int = 265598,
    available_expirations: list[str] | None = None,
    iv_30d: float | None = None,
    hist_vol: float | None = None,
    iv_hv_ratio: float | None = None,
    dividends_forward: float | None = None,
    dividends_ttm: float | None = None,
) -> Stock:
    """
    Create a Stock instance with customizable parameters.

    Args:
        symbol: Ticker symbol
        current_price: Current market price
        conid: IBKR contract ID
        available_expirations: List of expiration dates in DDMMMYY format (defaults to 15JAN26, 15FEB26, 15MAR26)
        iv_30d: 30-day implied volatility % (IBKR field 7283)
        hist_vol: 30-day historical volatility % (IBKR field 7087)
        iv_hv_ratio: IV/HV ratio as percentage (IBKR field 7084)
        dividends_forward: Expected dividends per share next 12 months (IBKR field 7671)
        dividends_ttm: Dividends per share last 12 months (IBKR field 7672)

    Returns:
        Stock instance with specified parameters
    """
    if available_expirations is None:
        available_expirations = ["15JAN26", "15FEB26", "15MAR26"]

    return Stock(
        symbol=symbol,
        current_price=current_price,
        conid=conid,
        available_expirations=available_expirations,
        iv_30d=iv_30d,
        hist_vol=hist_vol,
        iv_hv_ratio=iv_hv_ratio,
        dividends_forward=dividends_forward,
        dividends_ttm=dividends_ttm,
    )


# ============================================================================
# Option Contract Fixtures
# ============================================================================

def make_option_contract(
    conid: int,
    strike: float,
    right: Literal["C", "P"],
    expiration: date | None = None,
    bid: float | None = None,
    ask: float | None = None,
    multiplier: int = 100,
    delta: float | None = None,
    gamma: float | None = None,
    theta: float | None = None,
    vega: float | None = None,
    implied_volatility: float | None = None,
) -> OptionContract:
    """
    Create an OptionContract with sensible defaults.

    Args:
        conid: IBKR contract ID
        strike: Strike price
        right: "C" for call, "P" for put
        expiration: Expiration date (defaults to 30 days from today)
        bid: Bid price (defaults to intrinsic + small premium)
        ask: Ask price (defaults to bid + spread)
        multiplier: Contract multiplier (default 100)
        delta: Option delta (IBKR field 7308)
        gamma: Option gamma (IBKR field 7309)
        theta: Option theta (IBKR field 7310)
        vega: Option vega (IBKR field 7311)
        implied_volatility: Per-strike IV% (IBKR field 7633)

    Returns:
        OptionContract instance
    """
    if expiration is None:
        expiration = date.today() + timedelta(days=30)

    return OptionContract(
        conid=conid,
        strike=strike,
        right=right,
        expiration=expiration,
        bid=bid,
        ask=ask,
        multiplier=multiplier,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        implied_volatility=implied_volatility,
    )


def make_option_chain(
    expiration: date | None = None,
    strikes: list[float] | None = None,
    base_price: float = 150.0,
    call_conid_start: int = 100000,
    put_conid_start: int = 200000,
) -> OptionChain:
    """
    Create an OptionChain with realistic bid/ask spreads.

    Args:
        expiration: Expiration date (defaults to 30 days from today)
        strikes: List of strike prices (defaults to [140, 150, 160])
        base_price: Underlying stock price for premium calculation
        call_conid_start: Starting conid for calls
        put_conid_start: Starting conid for puts

    Returns:
        OptionChain with calls and puts at each strike

    Example:
        # Minimal chain (1 strike)
        chain = make_option_chain(strikes=[150.0])

        # Comprehensive chain (3 strikes)
        chain = make_option_chain(strikes=[140.0, 150.0, 160.0])
    """
    if expiration is None:
        expiration = date.today() + timedelta(days=30)
    if strikes is None:
        strikes = [140.0, 150.0, 160.0]

    calls = []
    puts = []

    for i, strike in enumerate(strikes):
        # Calculate realistic premiums based on moneyness
        # ITM options have higher premiums, OTM have lower

        # Call premiums (valuable when strike < base_price)
        call_intrinsic = max(0, base_price - strike)
        call_time_value = 3.0 + (base_price - strike) * 0.1  # More time value when near ATM
        call_mid = call_intrinsic + call_time_value
        call_bid = max(0.1, call_mid - 0.25)
        call_ask = call_mid + 0.25

        # Put premiums (valuable when strike > base_price)
        put_intrinsic = max(0, strike - base_price)
        put_time_value = 3.0 + (strike - base_price) * 0.1
        put_mid = put_intrinsic + put_time_value
        put_bid = max(0.1, put_mid - 0.25)
        put_ask = put_mid + 0.25

        calls.append(
            OptionContract(
                conid=call_conid_start + i,
                strike=strike,
                right="C",
                expiration=expiration,
                bid=round(call_bid, 2),
                ask=round(call_ask, 2),
                multiplier=100,
            )
        )

        puts.append(
            OptionContract(
                conid=put_conid_start + i,
                strike=strike,
                right="P",
                expiration=expiration,
                bid=round(put_bid, 2),
                ask=round(put_ask, 2),
                multiplier=100,
            )
        )

    return OptionChain(
        expiration=expiration,
        calls=calls,
        puts=puts,
    )


# ============================================================================
# Historical Data Fixtures
# ============================================================================

def make_historical_data(
    symbol: str = "AAPL",
    days: int = 252 * 5,  # 5 years of trading days
    base_price: float = 100.0,
    annual_return: float = 0.125,  # 12.5%
    annual_volatility: float = 0.20,  # 20%
    seed: int | None = None,
) -> dict:
    """
    Generate realistic historical price data using geometric Brownian motion.

    Args:
        symbol: Stock symbol
        days: Number of trading days
        base_price: Starting price
        annual_return: Expected annual return
        annual_volatility: Annual volatility (standard deviation)
        seed: Random seed for reproducibility

    Returns:
        Dict with format: {"closes": [{"close": float}, ...]}

    Example:
        # Reproducible data for tests
        hist = make_historical_data(seed=42)

        # Custom volatility
        hist = make_historical_data(annual_volatility=0.30)
    """
    if seed is not None:
        np.random.seed(seed)

    # Convert annual parameters to daily
    daily_return = annual_return / 252
    daily_volatility = annual_volatility / np.sqrt(252)

    # Generate returns using geometric Brownian motion
    daily_returns = np.random.normal(daily_return, daily_volatility, days)
    prices = base_price * np.exp(np.cumsum(daily_returns))

    return {
        "symbol": symbol,
        "closes": [{"close": float(price)} for price in prices]
    }


# ============================================================================
# Error Case Fixtures
# ============================================================================

def make_stock_not_found_error() -> dict:
    """Create error response for stock not found."""
    return {
        "error": "SYMBOL_NOT_FOUND",
        "message": "Symbol not found or not tradeable",
    }


def make_option_chain_empty(expiration: date | None = None) -> OptionChain:
    """
    Create an empty option chain (no liquid options available).

    Args:
        expiration: Expiration date

    Returns:
        OptionChain with no calls or puts
    """
    if expiration is None:
        expiration = date.today() + timedelta(days=30)

    return OptionChain(
        expiration=expiration,
        calls=[],
        puts=[],
    )


def make_option_contract_no_bid_ask(
    conid: int,
    strike: float,
    right: Literal["C", "P"],
    expiration: date | None = None,
) -> OptionContract:
    """
    Create an OptionContract with missing bid/ask (illiquid).

    Useful for testing error handling when price data is unavailable.
    """
    if expiration is None:
        expiration = date.today() + timedelta(days=30)

    return OptionContract(
        conid=conid,
        strike=strike,
        right=right,
        expiration=expiration,
        bid=None,
        ask=None,
        multiplier=100,
    )


# ============================================================================
# Preset Scenarios
# ============================================================================

# Simple preset (for fast integration tests)
SIMPLE_STOCK = make_stock()
SIMPLE_CHAIN = make_option_chain(strikes=[150.0])

# Comprehensive preset (for thorough e2e tests)
COMPREHENSIVE_STOCK = make_stock()
COMPREHENSIVE_CHAIN = make_option_chain(strikes=[140.0, 150.0, 160.0])

# Historical data preset (for analysis tests)
SAMPLE_HISTORICAL_DATA = make_historical_data(seed=42)
