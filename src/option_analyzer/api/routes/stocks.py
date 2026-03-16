"""
Stock and option chain endpoints.

Provides stateless market data retrieval from IBKR.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from ...clients.ibkr import IBKRClient
from ..dependencies import get_ibkr_client
from ..schemas import OptionChainResponse, OptionContractResponse, StockResponse

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/{symbol}", response_model=StockResponse)
async def get_stock(
    symbol: str,
    ibkr: Annotated[IBKRClient, Depends(get_ibkr_client)],
) -> StockResponse:
    """
    Get stock information by symbol.

    Args:
        symbol: Stock ticker symbol (e.g., "AAPL")
        ibkr: IBKR client dependency

    Returns:
        Stock information including current price and available option expirations

    Raises:
        404: Symbol not found
        502: IBKR API error

    Example:
        GET /api/stocks/AAPL
        Response: {
            "symbol": "AAPL",
            "current_price": 150.25,
            "conid": 265598,
            "available_expirations": ["JAN26", "FEB26", "MAR26"]
        }
    """
    stock = await ibkr.get_stock(symbol.upper())
    return StockResponse(
        symbol=stock.symbol,
        current_price=stock.current_price,
        conid=stock.conid,
        available_expirations=stock.available_expirations,
        iv_30d=stock.iv_30d,
        hist_vol=stock.hist_vol,
        iv_hv_ratio=stock.iv_hv_ratio,
        dividends_forward=stock.dividends_forward,
        dividends_ttm=stock.dividends_ttm,
    )


@router.get("/{symbol}/chains", response_model=OptionChainResponse)
async def get_option_chain(
    symbol: str,
    month: str,
    ibkr: Annotated[IBKRClient, Depends(get_ibkr_client)],
) -> OptionChainResponse:
    """
    Get option chain for a symbol and expiration month.

    Args:
        symbol: Stock ticker symbol (e.g., "AAPL")
        month: Expiration month (e.g., "JAN26", "FEB26")
        ibkr: IBKR client dependency

    Returns:
        Option chain with calls and puts for the specified expiration

    Raises:
        404: Symbol not found
        400: Invalid month format
        502: IBKR API error

    Example:
        GET /api/stocks/AAPL/chains?month=JAN26
        Response: {
            "expiration": "2026-01-16",
            "calls": [
                {
                    "conid": 123456,
                    "strike": 150.0,
                    "right": "C",
                    "expiration": "2026-01-16",
                    "bid": 2.50,
                    "ask": 2.55,
                    "multiplier": 100
                }
            ],
            "puts": [...]
        }
    """
    # Get stock first to get conid
    stock = await ibkr.get_stock(symbol.upper())

    # Get option chain for the specified month
    chain = await ibkr.get_option_chain(stock.conid, month.upper())

    # Convert to response format
    calls = [
        OptionContractResponse(
            conid=c.conid,
            strike=c.strike,
            right=c.right,
            expiration=c.expiration,
            bid=c.bid,
            ask=c.ask,
            multiplier=c.multiplier,
            delta=c.delta,
            gamma=c.gamma,
            theta=c.theta,
            vega=c.vega,
            implied_volatility=c.implied_volatility,
        )
        for c in chain.calls
    ]

    puts = [
        OptionContractResponse(
            conid=p.conid,
            strike=p.strike,
            right=p.right,
            expiration=p.expiration,
            bid=p.bid,
            ask=p.ask,
            multiplier=p.multiplier,
            delta=p.delta,
            gamma=p.gamma,
            theta=p.theta,
            vega=p.vega,
            implied_volatility=p.implied_volatility,
        )
        for p in chain.puts
    ]

    return OptionChainResponse(
        expiration=chain.expiration,
        calls=calls,
        puts=puts,
    )
