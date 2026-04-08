"""
API request and response schemas.

These Pydantic models define the contract between the API and clients.
"""

from datetime import date

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """
    Standardized error response format.

    Attributes:
        error: Human-readable error message
        code: Machine-readable error code (SNAKE_CASE)
        details: Optional additional context
    """

    error: str = Field(description="Human-readable error message")
    code: str = Field(description="Machine-readable error code")
    details: dict[str, str] | None = Field(
        default=None, description="Optional additional error context"
    )


class HealthCheckResponse(BaseModel):
    """
    Health check endpoint response.

    Attributes:
        status: Current service status
        version: API version
    """

    status: str = Field(description="Service status", examples=["healthy"])
    version: str = Field(description="API version", examples=["0.1.0"])


class StockResponse(BaseModel):
    """
    Stock information response.

    Attributes:
        symbol: Stock ticker symbol
        current_price: Current market price
        conid: IBKR contract identifier
        available_expirations: Available option expiration months
        iv_30d: 30-day implied volatility % of the underlying (IBKR field 7283)
        hist_vol: 30-day historical volatility % of the underlying (IBKR field 7087)
        iv_hv_ratio: IV/HV ratio as a percentage (IBKR field 7084)
        dividends_forward: Expected dividends per share over the next 12 months (IBKR field 7671)
        dividends_ttm: Dividends per share paid over the last 12 months (IBKR field 7672)
    """

    symbol: str = Field(description="Stock ticker symbol", examples=["AAPL"])
    current_price: float = Field(description="Current market price", examples=[150.25])
    conid: int = Field(description="IBKR contract identifier")
    available_expirations: list[str] = Field(
        description="Available option expiration months", examples=[["JAN26", "FEB26"]]
    )
    iv_30d: float | None = Field(
        default=None, description="30-day implied volatility % (IBKR field 7283)", examples=[28.5]
    )
    hist_vol: float | None = Field(
        default=None, description="30-day historical volatility % (IBKR field 7087)", examples=[22.3]
    )
    iv_hv_ratio: float | None = Field(
        default=None, description="IV/HV ratio as percentage (IBKR field 7084)", examples=[127.9]
    )
    dividends_forward: float | None = Field(
        default=None,
        description="Expected dividends per share next 12 months (IBKR field 7671)",
        examples=[0.96],
    )
    dividends_ttm: float | None = Field(
        default=None,
        description="Dividends per share last 12 months (IBKR field 7672)",
        examples=[0.92],
    )


class OptionContractResponse(BaseModel):
    """
    Option contract details.

    Attributes:
        conid: IBKR contract identifier
        strike: Strike price
        right: Option type (C=call, P=put)
        expiration: Expiration date
        bid: Current bid price per share
        ask: Current ask price per share
        multiplier: Shares per contract
        delta: Rate of change of option price per $1 move in underlying (IBKR field 7308)
        gamma: Rate of change of delta per $1 move in underlying (IBKR field 7309)
        theta: Daily time decay in dollars (IBKR field 7310)
        vega: Price change per 1% move in implied volatility (IBKR field 7311)
        implied_volatility: Per-strike implied volatility % (IBKR field 7633)
    """

    conid: int = Field(description="IBKR contract identifier")
    strike: float = Field(description="Strike price", examples=[150.0])
    right: str = Field(description="Option type", examples=["C", "P"])
    expiration: date = Field(description="Expiration date")
    bid: float | None = Field(description="Bid price per share", examples=[2.50])
    ask: float | None = Field(description="Ask price per share", examples=[2.55])
    multiplier: int = Field(description="Shares per contract", examples=[100])
    delta: float | None = Field(default=None, description="Delta (IBKR field 7308)", examples=[0.45])
    gamma: float | None = Field(default=None, description="Gamma (IBKR field 7309)", examples=[0.02])
    theta: float | None = Field(default=None, description="Theta (IBKR field 7310)", examples=[-0.05])
    vega: float | None = Field(default=None, description="Vega (IBKR field 7311)", examples=[0.15])
    implied_volatility: float | None = Field(
        default=None,
        description="Per-strike implied volatility % (IBKR field 7633)",
        examples=[28.5],
    )


class OptionChainResponse(BaseModel):
    """
    Option chain for a specific expiration.

    Attributes:
        expiration: Option expiration date
        calls: List of call option contracts
        puts: List of put option contracts
    """

    expiration: date = Field(description="Option expiration date")
    calls: list[OptionContractResponse] = Field(description="Call option contracts")
    puts: list[OptionContractResponse] = Field(description="Put option contracts")


class CloseEntry(BaseModel):
    """A single daily closing price entry."""

    date: str = Field(description="ISO date string", examples=["2024-03-15"])
    close: float = Field(description="Closing price", examples=[150.25])


class VolatilityHistoryResponse(BaseModel):
    """
    Historical price closes for realized volatility computation.

    Attributes:
        symbol: Stock ticker symbol
        closes: Daily closing prices sorted oldest-first
        current_iv: Current 30-day implied volatility % (IBKR field 7283)
    """

    symbol: str = Field(description="Stock ticker symbol", examples=["AAPL"])
    closes: list[CloseEntry] = Field(description="Daily closing prices, oldest first")
    current_iv: float | None = Field(
        default=None,
        description="Current 30-day implied volatility % (IBKR field 7283)",
        examples=[28.5],
    )


class StrategyInitRequest(BaseModel):
    """
    Request to initialize a new strategy.

    Attributes:
        symbol: Stock ticker symbol
    """

    symbol: str = Field(
        description="Stock ticker symbol",
        examples=["AAPL"],
        min_length=1,
        max_length=10,
    )


class StrategyInitResponse(BaseModel):
    """
    Response from strategy initialization.

    Attributes:
        symbol: Stock ticker symbol
        current_price: Current stock price
        target_date: Automatically selected target expiration date
        available_expirations: All available expiration months
        session_id: Session ID for subsequent requests
        iv_30d: 30-day implied volatility % of the underlying (IBKR field 7283)
        hist_vol: 30-day historical volatility % of the underlying (IBKR field 7087)
        iv_hv_ratio: IV/HV ratio as a percentage (IBKR field 7084)
        dividends_forward: Expected dividends per share over the next 12 months (IBKR field 7671)
        dividends_ttm: Dividends per share paid over the last 12 months (IBKR field 7672)
    """

    symbol: str = Field(description="Stock ticker symbol")
    current_price: float = Field(description="Current stock price")
    target_date: str = Field(
        description="Automatically selected target expiration (earliest)",
        examples=["JAN26"],
    )
    available_expirations: list[str] = Field(
        description="All available expiration months"
    )
    session_id: str = Field(description="Session ID for subsequent requests")
    iv_30d: float | None = Field(default=None, description="30-day implied volatility %")
    hist_vol: float | None = Field(default=None, description="30-day historical volatility %")
    iv_hv_ratio: float | None = Field(default=None, description="IV/HV ratio as percentage")
    dividends_forward: float | None = Field(
        default=None, description="Expected dividends per share next 12 months"
    )
    dividends_ttm: float | None = Field(
        default=None, description="Dividends per share last 12 months"
    )


class AddPositionRequest(BaseModel):
    """
    Request to add an option position to the strategy.

    Attributes:
        conid: IBKR contract identifier for the option
        quantity: Number of contracts (positive=long, negative=short, cannot be 0)
    """

    conid: int = Field(description="IBKR contract identifier", examples=[123456])
    quantity: int = Field(
        description="Number of contracts (positive=long, negative=short)",
        examples=[1, -2],
    )


class ModifyPositionRequest(BaseModel):
    """
    Request to modify an existing position's quantity.

    Attributes:
        quantity: New quantity (positive=long, negative=short, cannot be 0)
    """

    quantity: int = Field(
        description="New quantity (positive=long, negative=short)",
        examples=[2, -1],
    )


class PositionResponse(BaseModel):
    """
    Option position details.

    Attributes:
        conid: IBKR contract identifier
        strike: Strike price
        right: Option type (C=call, P=put)
        expiration: Expiration date
        quantity: Number of contracts
        bid: Bid price per share
        ask: Ask price per share
        delta: Delta at time of position entry (IBKR field 7308)
        gamma: Gamma at time of position entry (IBKR field 7309)
        theta: Theta at time of position entry (IBKR field 7310)
        vega: Vega at time of position entry (IBKR field 7311)
        implied_volatility: Per-strike IV% at time of position entry (IBKR field 7633)
    """

    conid: int = Field(description="IBKR contract identifier")
    strike: float = Field(description="Strike price")
    right: str = Field(description="Option type", examples=["C", "P"])
    expiration: date = Field(description="Expiration date")
    quantity: int = Field(description="Number of contracts")
    bid: float | None = Field(description="Bid price per share")
    ask: float | None = Field(description="Ask price per share")
    delta: float | None = Field(default=None, description="Delta (IBKR field 7308)")
    gamma: float | None = Field(default=None, description="Gamma (IBKR field 7309)")
    theta: float | None = Field(default=None, description="Theta (IBKR field 7310)")
    vega: float | None = Field(default=None, description="Vega (IBKR field 7311)")
    implied_volatility: float | None = Field(
        default=None, description="Per-strike IV% (IBKR field 7633)"
    )


class PositionsResponse(BaseModel):
    """
    List of all positions in the strategy.

    Attributes:
        positions: List of option positions
    """

    positions: list[PositionResponse] = Field(description="List of option positions")


class PriceBinResponse(BaseModel):
    """
    A histogram bin representing a price range and frequency.

    Attributes:
        lower: Lower bound of the price range
        upper: Upper bound of the price range
        count: Number of simulated outcomes in this range
        midpoint: Midpoint of the price range
    """

    lower: float = Field(description="Lower bound of price range", examples=[145.0])
    upper: float = Field(description="Upper bound of price range", examples=[150.0])
    count: int = Field(description="Number of outcomes in this bin", examples=[127])
    midpoint: float = Field(description="Midpoint of the bin", examples=[147.5])


class StrategyAnalysisResponse(BaseModel):
    """
    Strategy analysis results including Monte Carlo simulation.

    Attributes:
        price_distribution: Histogram bins of simulated price outcomes
        expected_value: Probability-weighted average P&L in dollars
        probability_of_profit: Fraction of outcomes with positive P&L (0.0 to 1.0)
        max_gain: Maximum possible profit (None if unlimited upside)
        max_loss: Maximum possible loss (None if unlimited downside)
        plot_url: URL path to the generated strategy chart
    """

    price_distribution: list[PriceBinResponse] = Field(
        description="Histogram of simulated price outcomes"
    )
    expected_value: float = Field(
        description="Probability-weighted average P&L", examples=[250.50]
    )
    probability_of_profit: float = Field(
        description="Fraction of profitable outcomes", ge=0.0, le=1.0, examples=[0.68]
    )
    max_gain: float | None = Field(
        description="Maximum possible profit (None if unlimited)", examples=[1000.0]
    )
    max_loss: float | None = Field(
        description="Maximum possible loss (None if unlimited)", examples=[-500.0]
    )
    plot_url: str = Field(
        description="URL path to the generated chart",
        examples=["static/plots/abc123_20260103_120530.png"],
    )


class StrategySummaryResponse(BaseModel):
    """
    Current strategy summary without analysis.

    Attributes:
        symbol: Stock ticker symbol
        current_price: Current stock price
        target_date: Target expiration date
        available_expirations: Available option expiration months
        stock_quantity: Number of shares in strategy
        positions: List of option positions
        iv_30d: 30-day implied volatility % of the underlying (IBKR field 7283)
        hist_vol: 30-day historical volatility % of the underlying (IBKR field 7087)
        iv_hv_ratio: IV/HV ratio as a percentage (IBKR field 7084)
        dividends_forward: Expected dividends per share over the next 12 months (IBKR field 7671)
        dividends_ttm: Dividends per share paid over the last 12 months (IBKR field 7672)
    """

    symbol: str = Field(description="Stock ticker symbol", examples=["AAPL"])
    current_price: float = Field(description="Current stock price", examples=[150.25])
    target_date: str = Field(
        description="Target expiration date", examples=["JAN26"]
    )
    available_expirations: list[str] = Field(
        description="Available option expiration months", examples=[["JAN26", "FEB26"]]
    )
    stock_quantity: int = Field(
        description="Number of shares (positive=long, negative=short, 0=no position)",
        default=0,
        examples=[100, -50, 0],
    )
    positions: list[PositionResponse] = Field(
        description="List of option positions", default_factory=list
    )
    iv_30d: float | None = Field(default=None, description="30-day implied volatility %")
    hist_vol: float | None = Field(default=None, description="30-day historical volatility %")
    iv_hv_ratio: float | None = Field(default=None, description="IV/HV ratio as percentage")
    dividends_forward: float | None = Field(
        default=None, description="Expected dividends per share next 12 months"
    )
    dividends_ttm: float | None = Field(
        default=None, description="Dividends per share last 12 months"
    )


class UpdateTargetDateRequest(BaseModel):
    """
    Request to update the target expiration date.

    Attributes:
        target_date: New target expiration date (must be in available_expirations)
    """

    target_date: str = Field(
        description="New target expiration date",
        examples=["FEB26"],
        min_length=1,
    )


class OptimizeRequest(BaseModel):
    """Request to run strategy optimizer on an option chain."""

    symbol: str = Field(description="Stock ticker symbol", examples=["AAPL"], min_length=1, max_length=10)
    expiration: str = Field(description="Target expiration month", examples=["JAN26"])
    max_loss_limit: float | None = Field(default=None, description="Skip strategies with max_loss worse than -limit", examples=[500.0])
    top_n: int = Field(default=20, description="Maximum results to return", ge=1, le=200)
    include_1leg: bool = Field(default=True, description="Include single-leg positions")
    include_2leg: bool = Field(default=True, description="Include all 2-leg combinations")
    include_named_multileg: bool = Field(default=True, description="Include iron condors, butterflies, etc.")
    include_stock_legs: bool = Field(default=True, description="Include stock + 0-2 option leg strategies")
    stock_qty: int = Field(default=100, description="Share count for stock-leg strategies", ge=1)


class OptimizeResultRow(BaseModel):
    """A single ranked strategy candidate from the optimizer."""

    rank: int = Field(description="1-based rank by ev_to_risk")
    description: str = Field(description="Human-readable strategy name", examples=["Bull Call Spread 490/510"])
    ev_to_risk: float = Field(description="EV divided by abs(max_loss)")
    ev: float = Field(description="Expected value in dollars")
    pop: float = Field(description="Probability of profit (0.0-1.0)")
    max_loss: float | None = Field(description="Maximum possible loss in dollars")
    max_gain: float | None = Field(description="Maximum possible gain in dollars")
    net_premium: float = Field(description="Net credit (positive) or debit (negative)")
    loss_unbounded: bool = Field(description="True when theoretical downside is unlimited")


class OptimizeResponse(BaseModel):
    """Results from strategy optimizer."""

    symbol: str
    expiration: str
    results: list[OptimizeResultRow]


class OptimizeChartResponse(BaseModel):
    """Chart URL for a single optimizer result."""

    plot_url: str = Field(description="URL path to the generated chart")


class UpdateStockQuantityRequest(BaseModel):
    """
    Request to update the stock quantity in the strategy.

    Attributes:
        stock_quantity: Number of shares (positive=long, negative=short, 0=no position)
    """

    stock_quantity: int = Field(
        description="Number of shares (positive=long, negative=short, 0=no position)",
        examples=[100, -50, 0],
    )
