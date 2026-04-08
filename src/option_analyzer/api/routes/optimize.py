"""
Strategy optimizer endpoints.

Runs the optimizer over a full option chain and returns ranked strategy
candidates by EV-to-risk ratio. Results are stored in the session so
individual charts can be generated lazily on demand.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, Response

from ...clients.ibkr import IBKRClient
from ...models.domain import OptionContract, OptionPosition, Stock, Strategy
from ...models.session import SessionState
from ...services.optimizer import StrategyResult, optimize_strategies
from ...services.session import SessionService
from ...services.statistics import PriceBin, create_histogram, geometric_returns, get_price_distribution
from ...utils.exceptions import ValidationError
from ...utils.plotting import cleanup_plot, create_strategy_chart, run_plot_operation, save_plot
from ..dependencies import (
    get_current_session,
    get_ibkr_client,
    get_plot_executor_dep,
    get_session_service_dep,
)
from ..schemas import (
    OptimizeChartResponse,
    OptimizeRequest,
    OptimizeResponse,
    OptimizeResultRow,
)

router = APIRouter(prefix="/api/optimize", tags=["optimize"])


def _strategy_from_result_data(data: dict) -> Strategy:
    """Reconstruct a Strategy domain object from a serialized StrategyResult dict."""
    s = data["strategy"]
    stock = Stock(
        symbol=s["stock"]["symbol"],
        current_price=s["stock"]["current_price"],
        conid=s["stock"]["conid"],
        available_expirations=s["stock"].get("available_expirations", []),
    )
    positions = []
    for p in s.get("option_positions", []):
        c = p["contract"]
        contract = OptionContract(
            conid=c["conid"],
            strike=c["strike"],
            right=c["right"],
            expiration=date.fromisoformat(c["expiration"]) if isinstance(c["expiration"], str) else c["expiration"],
            bid=c.get("bid"),
            ask=c.get("ask"),
            delta=c.get("delta"),
            gamma=c.get("gamma"),
            theta=c.get("theta"),
            vega=c.get("vega"),
            implied_volatility=c.get("implied_volatility"),
        )
        positions.append(OptionPosition(contract=contract, quantity=p["quantity"]))
    return Strategy(
        stock=stock,
        stock_quantity=s.get("stock_quantity", 0),
        option_positions=positions,
    )


def _get_optimize_session(session: SessionState) -> dict:
    data = session.data.get("optimize")
    if not data:
        raise ValidationError(
            "No optimizer results found. Call POST /api/optimize first.",
            code="NO_OPTIMIZE_RESULTS",
        )
    return data


@router.post("", response_model=OptimizeResponse)
async def run_optimizer(
    request: OptimizeRequest,
    response: Response,
    ibkr: Annotated[IBKRClient, Depends(get_ibkr_client)],
    session_service: Annotated[SessionService, Depends(get_session_service_dep)],
) -> OptimizeResponse:
    """
    Run the strategy optimizer over a full option chain.

    Fetches the option chain, runs Monte Carlo simulation (same as the strategy
    analyzer), then enumerates and ranks all valid strategies by EV / abs(max_loss).
    Results are stored in the session for lazy chart generation.

    Returns the top N ranked strategies as a table-ready response.
    """
    symbol = request.symbol.upper()

    # Fetch stock and validate expirations
    stock = await ibkr.get_stock(symbol)
    if not stock.available_expirations:
        raise ValidationError(
            f"No option expirations available for '{symbol}'",
            code="NO_EXPIRATIONS_AVAILABLE",
        )
    if request.expiration not in stock.available_expirations:
        raise ValidationError(
            f"Expiration '{request.expiration}' not available for '{symbol}'. "
            f"Available: {', '.join(stock.available_expirations)}",
            code="INVALID_EXPIRATION",
        )

    # Fetch full option chain
    chain = await ibkr.get_option_chain(stock.conid, request.expiration)

    # Fetch 5 years of historical data and build price distribution
    historical_data = await ibkr.get_historical_data(conid=stock.conid, years=5)
    closes = np.array([entry["close"] for entry in historical_data["closes"]])
    returns = geometric_returns(closes)
    price_distribution = get_price_distribution(
        current_price=stock.current_price,
        returns=returns,
        target_date=chain.expiration,
        bootstrap_samples=10000,
    )
    bins = create_histogram(price_distribution, n_bins=50)

    # Run optimizer
    results = optimize_strategies(
        bins,
        chain,
        stock,
        include_1leg=request.include_1leg,
        include_2leg=request.include_2leg,
        include_named_multileg=request.include_named_multileg,
        include_stock_legs=request.include_stock_legs,
        stock_qty=request.stock_qty,
        max_loss_limit=request.max_loss_limit,
        top_n=request.top_n,
    )

    # Persist to session (create or reuse)
    session = session_service.create_session()
    response.set_cookie(key="session_id", value=session.session_id)

    session.data["optimize"] = {
        "symbol": symbol,
        "stock_conid": stock.conid,
        "current_price": stock.current_price,
        "expiration": request.expiration,
        "available_expirations": stock.available_expirations,
        "bins": [b.model_dump() for b in bins],
        "results": [r.model_dump() for r in results],
    }

    # Build response rows
    rows = [
        OptimizeResultRow(
            rank=i + 1,
            description=r.description,
            ev_to_risk=r.ev_to_risk,
            ev=r.metrics.expected_value,
            pop=r.metrics.probability_of_profit,
            max_loss=r.metrics.max_loss,
            max_gain=r.metrics.max_gain,
            net_premium=r.strategy.net_premium,
            loss_unbounded=r.loss_unbounded,
        )
        for i, r in enumerate(results)
    ]

    return OptimizeResponse(symbol=symbol, expiration=request.expiration, results=rows)


@router.get("/chart/{rank}", response_model=OptimizeChartResponse)
async def get_optimizer_chart(
    rank: int,
    session: Annotated[SessionState, Depends(get_current_session)],
    plot_executor: Annotated[ThreadPoolExecutor, Depends(get_plot_executor_dep)],
) -> OptimizeChartResponse:
    """
    Generate and return a P&L chart for a ranked optimizer result.

    Charts are generated lazily — only when a row is selected in the UI.
    Rank is 1-based.
    """
    opt = _get_optimize_session(session)
    results_data = opt["results"]

    idx = rank - 1
    if idx < 0 or idx >= len(results_data):
        raise ValidationError(
            f"Rank {rank} is out of range (1–{len(results_data)})",
            code="RANK_OUT_OF_RANGE",
        )

    bins = [PriceBin(**b) for b in opt["bins"]]
    strategy = _strategy_from_result_data(results_data[idx])

    def _create_chart():
        return create_strategy_chart(bins, strategy)

    fig = await run_plot_operation(plot_executor, _create_chart)
    try:
        plot_url = save_plot(fig, session.session_id, session=session)
    finally:
        cleanup_plot(fig)

    return OptimizeChartResponse(plot_url=plot_url)


@router.post("/load/{rank}", response_model=None, status_code=200)
async def load_optimizer_result(
    rank: int,
    session: Annotated[SessionState, Depends(get_current_session)],
) -> dict:
    """
    Load an optimizer result into the strategy builder session.

    Copies the strategy at the given rank into session.data['strategy']
    in the format expected by the strategy builder, then returns a redirect
    target so the frontend can navigate to /.
    """
    opt = _get_optimize_session(session)
    results_data = opt["results"]

    idx = rank - 1
    if idx < 0 or idx >= len(results_data):
        raise ValidationError(
            f"Rank {rank} is out of range (1–{len(results_data)})",
            code="RANK_OUT_OF_RANGE",
        )

    strategy = _strategy_from_result_data(results_data[idx])

    # Serialize into the strategy-builder session format
    positions = []
    for pos in strategy.option_positions:
        c = pos.contract
        positions.append({
            "conid": c.conid,
            "strike": c.strike,
            "right": c.right,
            "expiration": str(c.expiration),
            "quantity": pos.quantity,
            "bid": c.bid,
            "ask": c.ask,
            "delta": c.delta,
            "gamma": c.gamma,
            "theta": c.theta,
            "vega": c.vega,
            "implied_volatility": c.implied_volatility,
        })

    session.data["strategy"] = {
        "symbol": opt["symbol"],
        "stock_conid": opt["stock_conid"],
        "current_price": opt["current_price"],
        "target_date": opt["expiration"],
        "available_expirations": opt["available_expirations"],
        "stock_quantity": strategy.stock_quantity,
        "positions": positions,
    }

    return {"redirect": "/"}
