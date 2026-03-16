"""
Strategy management endpoints.

Provides endpoints for initializing and managing option trading strategies.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, Response

from ...clients.ibkr import IBKRClient
from ...models.domain import OptionContract, OptionPosition, Stock, Strategy
from ...models.session import SessionState
from ...services.risk import calculate_risk_metrics
from ...services.session import SessionService
from ...services.statistics import create_histogram, geometric_returns, get_price_distribution
from ...utils.exceptions import InvalidQuantityError, MixedExpirationError, ValidationError
from ...utils.plotting import cleanup_plot, create_strategy_chart, run_plot_operation, save_plot
from ..dependencies import (
    get_current_session,
    get_ibkr_client,
    get_plot_executor_dep,
    get_session_service_dep,
)
from ..schemas import (
    AddPositionRequest,
    ModifyPositionRequest,
    PositionResponse,
    PositionsResponse,
    PriceBinResponse,
    StrategyAnalysisResponse,
    StrategyInitRequest,
    StrategyInitResponse,
    StrategySummaryResponse,
    UpdateStockQuantityRequest,
    UpdateTargetDateRequest,
)

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.post("/init", response_model=StrategyInitResponse)
async def initialize_strategy(
    request: StrategyInitRequest,
    response: Response,
    ibkr: Annotated[IBKRClient, Depends(get_ibkr_client)],
    session_service: Annotated[SessionService, Depends(get_session_service_dep)],
) -> StrategyInitResponse:
    """
    Initialize a new trading strategy.

    Creates or updates a session with strategy data including the stock
    and automatically selected target expiration date (earliest available).

    Args:
        request: Strategy initialization request with symbol
        response: FastAPI response for setting cookies
        ibkr: IBKR client dependency
        session_service: Session service dependency

    Returns:
        Strategy initialization response with stock info and session ID

    Raises:
        404: Symbol not found
        400: No available option expirations for symbol
        502: IBKR API error

    Example:
        POST /api/strategy/init
        Body: {"symbol": "AAPL"}
        Response: {
            "symbol": "AAPL",
            "current_price": 150.25,
            "target_date": "JAN26",
            "available_expirations": ["JAN26", "FEB26", "MAR26"],
            "session_id": "abc123..."
        }
    """
    # Get stock info from IBKR
    stock = await ibkr.get_stock(request.symbol.upper())

    # Validate that stock has available option expirations
    if not stock.available_expirations:
        raise ValidationError(
            f"No option expirations available for symbol '{stock.symbol}'",
            code="NO_EXPIRATIONS_AVAILABLE",
        )

    # Select earliest expiration as target_date
    target_date = stock.available_expirations[0]

    # Create or get session
    session = session_service.create_session()

    # Store strategy data in session
    session.data["strategy"] = {
        "symbol": stock.symbol,
        "stock_conid": stock.conid,
        "current_price": stock.current_price,
        "target_date": target_date,
        "available_expirations": stock.available_expirations,
        "stock_quantity": 0,  # Initialize stock quantity
        "positions": [],  # Will be populated by position endpoints
        "iv_30d": stock.iv_30d,
        "hist_vol": stock.hist_vol,
        "iv_hv_ratio": stock.iv_hv_ratio,
        "dividends_forward": stock.dividends_forward,
        "dividends_ttm": stock.dividends_ttm,
    }

    # Set session cookie
    response.set_cookie(key="session_id", value=session.session_id)

    return StrategyInitResponse(
        symbol=stock.symbol,
        current_price=stock.current_price,
        target_date=target_date,
        available_expirations=stock.available_expirations,
        session_id=session.session_id,
        iv_30d=stock.iv_30d,
        hist_vol=stock.hist_vol,
        iv_hv_ratio=stock.iv_hv_ratio,
        dividends_forward=stock.dividends_forward,
        dividends_ttm=stock.dividends_ttm,
    )


@router.get("", response_model=StrategySummaryResponse)
async def get_strategy_summary(
    session: Annotated[SessionState, Depends(get_current_session)],
) -> StrategySummaryResponse:
    """
    Get current strategy summary.

    Returns the current strategy from session without performing analysis.
    This is a fast read operation that retrieves the stock, target date,
    and option positions.

    Args:
        session: Current session with strategy data

    Returns:
        Strategy summary with stock info and positions

    Raises:
        401: No valid session
        400: No strategy initialized

    Example:
        GET /api/strategy
        Response: {
            "symbol": "AAPL",
            "current_price": 150.25,
            "target_date": "JAN26",
            "available_expirations": ["JAN26", "FEB26", "MAR26"],
            "positions": [
                {"conid": 123456, "strike": 150.0, "right": "C", ...}
            ]
        }
    """
    # Get strategy from session
    strategy_data = session.data.get("strategy")
    if not strategy_data:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Convert positions to PositionResponse format
    positions = [PositionResponse(**pos) for pos in strategy_data.get("positions", [])]

    return StrategySummaryResponse(
        symbol=strategy_data["symbol"],
        current_price=strategy_data["current_price"],
        target_date=strategy_data["target_date"],
        available_expirations=strategy_data.get("available_expirations", []),
        stock_quantity=strategy_data.get("stock_quantity", 0),
        positions=positions,
        iv_30d=strategy_data.get("iv_30d"),
        hist_vol=strategy_data.get("hist_vol"),
        iv_hv_ratio=strategy_data.get("iv_hv_ratio"),
        dividends_forward=strategy_data.get("dividends_forward"),
        dividends_ttm=strategy_data.get("dividends_ttm"),
    )


@router.patch("/target-date", response_model=StrategyInitResponse)
async def update_target_date(
    request: UpdateTargetDateRequest,
    session: Annotated[SessionState, Depends(get_current_session)],
) -> StrategyInitResponse:
    """
    Update the target expiration date for the strategy.

    Changes the target expiration date to a different available expiration.
    Requires that no positions exist in the strategy (positions must be
    cleared before changing target date).

    Args:
        request: New target date (must be in available_expirations)
        session: Current session with strategy data

    Returns:
        Updated strategy information (similar to init response)

    Raises:
        401: No valid session
        400: No strategy initialized, invalid target date, or positions exist

    Example:
        PATCH /api/strategy/target-date
        Body: {"target_date": "FEB26"}
        Response: {
            "symbol": "AAPL",
            "current_price": 150.25,
            "target_date": "FEB26",
            "available_expirations": ["JAN26", "FEB26", "MAR26"],
            "session_id": "abc123..."
        }
    """
    # Get strategy from session
    strategy_data = session.data.get("strategy")
    if not strategy_data:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Validate target_date is in available_expirations
    available_expirations = strategy_data.get("available_expirations", [])
    if request.target_date not in available_expirations:
        raise ValidationError(
            f"Invalid target_date '{request.target_date}'. "
            f"Must be one of: {', '.join(available_expirations)}",
            code="INVALID_TARGET_DATE",
        )

    # Check if positions exist - reject if they do
    positions = strategy_data.get("positions", [])
    if positions:
        raise ValidationError(
            f"Cannot change target date when positions exist. "
            f"Please delete all {len(positions)} position(s) first.",
            code="POSITIONS_EXIST",
        )

    # Update target_date in session
    strategy_data["target_date"] = request.target_date

    return StrategyInitResponse(
        symbol=strategy_data["symbol"],
        current_price=strategy_data["current_price"],
        target_date=request.target_date,
        available_expirations=available_expirations,
        session_id=session.session_id,
        iv_30d=strategy_data.get("iv_30d"),
        hist_vol=strategy_data.get("hist_vol"),
        iv_hv_ratio=strategy_data.get("iv_hv_ratio"),
        dividends_forward=strategy_data.get("dividends_forward"),
        dividends_ttm=strategy_data.get("dividends_ttm"),
    )


@router.post("/positions", response_model=PositionsResponse)
async def add_position(
    request: AddPositionRequest,
    session: Annotated[SessionState, Depends(get_current_session)],
    ibkr: Annotated[IBKRClient, Depends(get_ibkr_client)],
) -> PositionsResponse:
    """
    Add an option position to the strategy.

    Args:
        request: Position details (conid and quantity)
        session: Current session with strategy data
        ibkr: IBKR client dependency

    Returns:
        Updated list of all positions

    Raises:
        401: No valid session
        400: Invalid quantity (zero), mixed expirations, or contract not found
        502: IBKR API error

    Example:
        POST /api/positions
        Body: {"conid": 123456, "quantity": 2}
        Response: {"positions": [...]}
    """
    # Validate quantity != 0
    if request.quantity == 0:
        raise InvalidQuantityError()

    # Get strategy from session
    strategy = session.data.get("strategy")
    if not strategy:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Get option chain for target_date to find the contract
    stock_conid = strategy["stock_conid"]
    target_date = strategy["target_date"]
    chain = await ibkr.get_option_chain(stock_conid, target_date)

    # Find contract by conid in the chain
    contract = None
    for c in list(chain.calls) + list(chain.puts):
        if c.conid == request.conid:
            contract = c
            break

    if not contract:
        raise ValidationError(
            f"Contract {request.conid} not found in option chain for {target_date}",
            code="CONTRACT_NOT_FOUND",
        )

    # Check for mixed expirations and duplicates
    positions = strategy.get("positions", [])
    if positions:
        # All positions must have same expiration as the new contract
        for pos in positions:
            if pos["expiration"] != str(contract.expiration):
                raise MixedExpirationError(
                    f"Cannot add contract with expiration {contract.expiration}. "
                    f"Strategy already has positions with expiration {pos['expiration']}"
                )

    # Check for duplicate conid
    for pos in positions:
        if pos["conid"] == request.conid:
            raise ValidationError(
                f"Position with conid {request.conid} already exists in strategy. "
                f"Use PATCH /api/strategy/positions/{request.conid} to modify the existing position.",
                code="DUPLICATE_POSITION",
            )

    # Add position to strategy
    position_data = {
        "conid": contract.conid,
        "strike": contract.strike,
        "right": contract.right,
        "expiration": str(contract.expiration),
        "quantity": request.quantity,
        "bid": contract.bid,
        "ask": contract.ask,
        "delta": contract.delta,
        "gamma": contract.gamma,
        "theta": contract.theta,
        "vega": contract.vega,
        "implied_volatility": contract.implied_volatility,
    }
    positions.append(position_data)
    strategy["positions"] = positions

    # Return all positions
    return PositionsResponse(
        positions=[PositionResponse(**pos) for pos in positions]
    )


@router.patch("/positions/{conid}", response_model=PositionsResponse)
async def modify_position(
    conid: int,
    request: ModifyPositionRequest,
    session: Annotated[SessionState, Depends(get_current_session)],
) -> PositionsResponse:
    """
    Modify an existing position's quantity.

    Args:
        conid: Contract ID of position to modify
        request: New quantity
        session: Current session with strategy data

    Returns:
        Updated list of all positions

    Raises:
        401: No valid session
        400: Invalid quantity (zero) or position not found

    Example:
        PATCH /api/positions/123456
        Body: {"quantity": 3}
        Response: {"positions": [...]}
    """
    # Validate quantity != 0
    if request.quantity == 0:
        raise InvalidQuantityError()

    # Get strategy from session
    strategy = session.data.get("strategy")
    if not strategy:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Find and update position
    positions = strategy.get("positions", [])
    found = False
    for pos in positions:
        if pos["conid"] == conid:
            pos["quantity"] = request.quantity
            found = True
            break

    if not found:
        raise ValidationError(
            f"Position with conid {conid} not found",
            code="POSITION_NOT_FOUND",
        )

    # Return all positions
    return PositionsResponse(
        positions=[PositionResponse(**pos) for pos in positions]
    )


@router.delete("/positions/{conid}", response_model=PositionsResponse)
async def delete_position(
    conid: int,
    session: Annotated[SessionState, Depends(get_current_session)],
) -> PositionsResponse:
    """
    Remove a position from the strategy.

    Args:
        conid: Contract ID of position to remove
        session: Current session with strategy data

    Returns:
        Updated list of remaining positions

    Raises:
        401: No valid session
        400: Position not found

    Example:
        DELETE /api/positions/123456
        Response: {"positions": [...]}
    """
    # Get strategy from session
    strategy = session.data.get("strategy")
    if not strategy:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Find and remove position
    positions = strategy.get("positions", [])
    original_length = len(positions)
    positions = [pos for pos in positions if pos["conid"] != conid]

    if len(positions) == original_length:
        raise ValidationError(
            f"Position with conid {conid} not found",
            code="POSITION_NOT_FOUND",
        )

    strategy["positions"] = positions

    # Return remaining positions
    return PositionsResponse(
        positions=[PositionResponse(**pos) for pos in positions]
    )


@router.post("/reset", response_model=StrategySummaryResponse)
async def reset_strategy(
    session: Annotated[SessionState, Depends(get_current_session)],
) -> StrategySummaryResponse:
    """
    Reset the current strategy.

    Clears all positions from the strategy and resets the target date
    to the earliest available expiration. Keeps the same session and
    stock symbol.

    Args:
        session: Current session with strategy data

    Returns:
        Strategy summary with cleared positions

    Raises:
        401: No valid session
        400: No strategy initialized

    Example:
        POST /api/strategy/reset
        Response: {
            "symbol": "AAPL",
            "current_price": 150.25,
            "target_date": "JAN26",
            "available_expirations": ["JAN26", "FEB26", "MAR26"],
            "positions": []
        }
    """
    # Get strategy from session
    strategy_data = session.data.get("strategy")
    if not strategy_data:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Clear all positions
    strategy_data["positions"] = []

    # Clear stock quantity
    strategy_data["stock_quantity"] = 0

    # Reset target_date to earliest expiration
    available_expirations = strategy_data.get("available_expirations", [])
    if available_expirations:
        strategy_data["target_date"] = available_expirations[0]

    return StrategySummaryResponse(
        symbol=strategy_data["symbol"],
        current_price=strategy_data["current_price"],
        target_date=strategy_data["target_date"],
        available_expirations=available_expirations,
        stock_quantity=0,
        positions=[],
        iv_30d=strategy_data.get("iv_30d"),
        hist_vol=strategy_data.get("hist_vol"),
        iv_hv_ratio=strategy_data.get("iv_hv_ratio"),
        dividends_forward=strategy_data.get("dividends_forward"),
        dividends_ttm=strategy_data.get("dividends_ttm"),
    )


@router.patch("/stock-quantity", response_model=StrategySummaryResponse)
async def update_stock_quantity(
    request: UpdateStockQuantityRequest,
    session: Annotated[SessionState, Depends(get_current_session)],
) -> StrategySummaryResponse:
    """
    Update the number of shares (stock quantity) in the strategy.

    Sets the stock position to the specified quantity. Positive values
    represent long positions, negative values represent short positions,
    and zero clears the stock position.

    Args:
        request: New stock quantity (positive=long, negative=short, 0=none)
        session: Current session with strategy data

    Returns:
        Updated strategy summary with new stock quantity

    Raises:
        401: No valid session
        400: No strategy initialized

    Example:
        PATCH /api/strategy/stock-quantity
        Body: {"stock_quantity": 100}
        Response: {
            "symbol": "AAPL",
            "current_price": 150.25,
            "target_date": "JAN26",
            "available_expirations": ["JAN26", "FEB26", "MAR26"],
            "stock_quantity": 100,
            "positions": [...]
        }
    """
    # Get strategy from session
    strategy_data = session.data.get("strategy")
    if not strategy_data:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Update stock_quantity in session
    strategy_data["stock_quantity"] = request.stock_quantity

    # Convert positions to PositionResponse format
    positions = [PositionResponse(**pos) for pos in strategy_data.get("positions", [])]

    return StrategySummaryResponse(
        symbol=strategy_data["symbol"],
        current_price=strategy_data["current_price"],
        target_date=strategy_data["target_date"],
        available_expirations=strategy_data.get("available_expirations", []),
        stock_quantity=request.stock_quantity,
        positions=positions,
        iv_30d=strategy_data.get("iv_30d"),
        hist_vol=strategy_data.get("hist_vol"),
        iv_hv_ratio=strategy_data.get("iv_hv_ratio"),
        dividends_forward=strategy_data.get("dividends_forward"),
        dividends_ttm=strategy_data.get("dividends_ttm"),
    )


def _reconstruct_strategy_from_session(session: SessionState) -> Strategy:
    """
    Reconstruct a Strategy domain object from session data.

    Args:
        session: Current session state containing strategy data

    Returns:
        Strategy object with Stock and OptionPositions

    Raises:
        ValidationError: If strategy data is missing or incomplete
    """
    strategy_data = session.data.get("strategy")
    if not strategy_data:
        raise ValidationError("No strategy initialized. Call /api/strategy/init first.")

    # Reconstruct Stock object
    stock = Stock(
        symbol=strategy_data["symbol"],
        current_price=strategy_data["current_price"],
        conid=strategy_data["stock_conid"],
        available_expirations=strategy_data.get("available_expirations", []),
    )

    # Reconstruct OptionPosition objects from positions data
    option_positions = []
    for pos_data in strategy_data.get("positions", []):
        contract = OptionContract(
            conid=pos_data["conid"],
            strike=pos_data["strike"],
            right=pos_data["right"],
            expiration=date.fromisoformat(pos_data["expiration"]),
            bid=pos_data.get("bid"),
            ask=pos_data.get("ask"),
            delta=pos_data.get("delta"),
            gamma=pos_data.get("gamma"),
            theta=pos_data.get("theta"),
            vega=pos_data.get("vega"),
            implied_volatility=pos_data.get("implied_volatility"),
        )
        position = OptionPosition(contract=contract, quantity=pos_data["quantity"])
        option_positions.append(position)

    # Create Strategy object
    return Strategy(
        stock=stock,
        stock_quantity=strategy_data.get("stock_quantity", 0),
        option_positions=option_positions,
    )


@router.post("/analyze", response_model=StrategyAnalysisResponse)
async def analyze_strategy(
    session: Annotated[SessionState, Depends(get_current_session)],
    ibkr: Annotated[IBKRClient, Depends(get_ibkr_client)],
    plot_executor: Annotated[ThreadPoolExecutor, Depends(get_plot_executor_dep)],
) -> StrategyAnalysisResponse:
    """
    Analyze strategy using Monte Carlo simulation.

    Performs risk analysis on the current strategy using historical price data
    and Monte Carlo simulation to generate a probability distribution of outcomes.

    Args:
        session: Current session with strategy data
        ibkr: IBKR client dependency

    Returns:
        Analysis results with price distribution and risk metrics

    Raises:
        401: No valid session
        400: No strategy initialized, mixed expirations, or missing price data
        502: IBKR API error

    Example:
        POST /api/strategy/analyze
        Response: {
            "price_distribution": [
                {"lower": 145.0, "upper": 150.0, "count": 127, "midpoint": 147.5},
                ...
            ],
            "expected_value": 250.50,
            "probability_of_profit": 0.68,
            "max_gain": 1000.0,
            "max_loss": -500.0
        }
    """
    # Reconstruct Strategy object from session
    strategy = _reconstruct_strategy_from_session(session)

    # Validate strategy is ready for analysis
    strategy.validate_for_analysis()

    # Get target expiration from session
    strategy_data = session.data.get("strategy", {})
    target_date_str = strategy_data.get("target_date")
    if not target_date_str:
        raise ValidationError("No target date found in strategy")

    # Get earliest expiration date from option positions, or fetch from option chain
    target_expiration = strategy.get_earliest_expiration()
    if not target_expiration:
        # For stock-only strategies, fetch option chain to get expiration date
        stock_conid = strategy.stock.conid
        chain = await ibkr.get_option_chain(stock_conid, target_date_str)
        target_expiration = chain.expiration

    # Fetch historical data from IBKR
    historical_data = await ibkr.get_historical_data(
        conid=strategy.stock.conid,
        years=5,  # Use 5 years of historical data
    )

    # Extract closing prices and calculate geometric returns
    closes = np.array([entry["close"] for entry in historical_data["closes"]])
    returns = geometric_returns(closes)

    # Generate price distribution using Monte Carlo
    price_distribution = get_price_distribution(
        current_price=strategy.stock.current_price,
        returns=returns,
        target_date=target_expiration,
        bootstrap_samples=10000,  # 10k Monte Carlo simulations
    )

    # Create histogram bins
    bins = create_histogram(price_distribution, n_bins=50)

    # Calculate risk metrics
    risk_metrics = calculate_risk_metrics(bins, strategy)

    # Convert bins to response format
    bin_responses = [
        PriceBinResponse(
            lower=bin.lower,
            upper=bin.upper,
            count=bin.count,
            midpoint=bin.midpoint,
        )
        for bin in bins
    ]

    # Generate strategy chart with price distribution and P&L curve
    def _create_chart():
        return create_strategy_chart(bins, strategy)

    fig = await run_plot_operation(plot_executor, _create_chart)

    try:
        # Save plot to disk with session-based naming
        plot_url = save_plot(fig, session.session_id, session=session)
    finally:
        # Always cleanup figure to prevent memory leaks
        cleanup_plot(fig)

    return StrategyAnalysisResponse(
        price_distribution=bin_responses,
        expected_value=risk_metrics.expected_value,
        probability_of_profit=risk_metrics.probability_of_profit,
        max_gain=risk_metrics.max_gain,
        max_loss=risk_metrics.max_loss,
        plot_url=plot_url,
    )
