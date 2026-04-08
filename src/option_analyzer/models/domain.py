"""
Core domain models for option analysis.

These models represent the fundamental business objects:
- Stock: Underlying security
- OptionContract: Individual option contract details
- OptionChain: Complete option chain for a symbol and expiration
- OptionPosition: A position (long/short) in an option contract
- Strategy: Complete trading strategy with stock and option positions
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..utils.exceptions import (
    InvalidQuantityError,
    MissingBidAskError,
    MixedExpirationError,
    ValidationError,
)


class Stock(BaseModel):
    """
    Underlying stock information.

    Attributes:
        symbol: Stock ticker symbol (e.g., "AAPL")
        current_price: Current market price per share
        conid: IBKR contract identifier
        available_expirations: List of available option expiration dates
        iv_30d: 30-day implied volatility of the underlying (field 7283), as a percentage
        hist_vol: 30-day historical volatility of the underlying (field 7087), as a percentage
        iv_hv_ratio: IV/HV ratio expressed as a percentage (field 7084)
        dividends_forward: Expected dividends over the next 12 months per share (field 7671)
        dividends_ttm: Dividends paid over the last 12 months per share (field 7672)
    """

    symbol: str
    current_price: float = Field(gt=0, description="Current stock price (must be positive)")
    conid: int # @todo l3b
    available_expirations: list[str] = Field(default_factory=list)
    iv_30d: float | None = None
    hist_vol: float | None = None
    iv_hv_ratio: float | None = None
    dividends_forward: float | None = None
    dividends_ttm: float | None = None

    def payoff_at_price(self, price: float) -> float:
        """
        Calculate per-share gain/loss at a given price.

        Args:
            price: Future stock price

        Returns:
            Gain or loss per share
        """
        return price - self.current_price


class OptionContract(BaseModel):
    """
    Individual option contract specification.

    Attributes:
        conid: IBKR contract identifier
        strike: Strike price
        right: Option type (C=call, P=put)
        expiration: Expiration date
        bid: Current bid price (per share, not per contract)
        ask: Current ask price (per share, not per contract)
        multiplier: Shares per contract (typically 100)
    """

    conid: int # @todo l3b
    strike: float = Field(gt=0)
    right: Literal["C", "P"]
    expiration: date
    bid: float | None = Field(default=None, ge=0)
    ask: float | None = Field(default=None, ge=0)
    multiplier: int = Field(default=100, gt=0)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    implied_volatility: float | None = None

    def intrinsic_value(self, price: float) -> float:
        """
        Calculate intrinsic value at expiration (per contract).

        Args:
            price: Underlying stock price

        Returns:
            Intrinsic value in dollars (includes multiplier)

        Note:
            This is the value at expiration with no time value.
        """
        if self.right == "C":
            # Call: max(price - strike, 0) * multiplier
            return max(0, price - self.strike) * self.multiplier
        else:
            # Put: max(strike - price, 0) * multiplier
            return max(0, self.strike - price) * self.multiplier

    def days_to_expiry(self, reference_date: date | None = None) -> int:
        """
        Calculate calendar days until expiration.

        Args:
            reference_date: Reference date (defaults to today)

        Returns:
            Number of calendar days to expiration

        Raises:
            ValueError: If reference_date is after expiration (option has expired)
        """
        ref = reference_date or date.today()
        days = (self.expiration - ref).days

        if days < 0:
            raise ValueError(
                f"Option has expired: reference_date ({ref}) is after "
                f"expiration ({self.expiration})"
            )

        return days


class OptionChain(BaseModel):
    """
    Complete option chain for an expiration date.

    Attributes:
        expiration: Option expiration date
        calls: List of call option contracts
        puts: List of put option contracts
    """

    expiration: date
    calls: list[OptionContract] = Field(default_factory=list)
    puts: list[OptionContract] = Field(default_factory=list)


class OptionPosition(BaseModel):
    """
    A position in an option contract.

    Attributes:
        contract: The option contract details
        quantity: Number of contracts (positive=long, negative=short)

    Raises:
        InvalidQuantityError: If quantity is zero
    """

    contract: OptionContract
    quantity: int = Field(description="Number of contracts (positive=long, negative=short)")

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: int) -> int:
        """Validate that quantity is non-zero."""
        if v == 0:
            raise InvalidQuantityError()
        return v

    @property
    def premium_paid(self) -> float:
        """
        Total upfront cost/credit for this position.

        Returns:
            Negative value for long positions (cost)
            Positive value for short positions (credit)

        Raises:
            MissingBidAskError: If required bid/ask price data is unavailable

        Note:
            - Long positions pay the ask price
            - Short positions receive the bid price
            - Result includes the multiplier (e.g., 100 shares per contract)
        """
        if self.quantity > 0:
            # Long: pay ask (negative cash flow)
            if self.contract.ask is None:
                raise MissingBidAskError(
                    f"Missing ask price for contract {self.contract.conid} "
                    f"({self.contract.strike} {self.contract.right} {self.contract.expiration})"
                )
            return -self.quantity * self.contract.ask * self.contract.multiplier
        else:
            # Short: receive bid (positive cash flow)
            if self.contract.bid is None:
                raise MissingBidAskError(
                    f"Missing bid price for contract {self.contract.conid} "
                    f"({self.contract.strike} {self.contract.right} {self.contract.expiration})"
                )
            return -self.quantity * self.contract.bid * self.contract.multiplier

    def payoff_at_price(self, price: float) -> float:
        """
        Calculate total P&L at a given underlying price.

        Args:
            price: Underlying stock price at expiration

        Returns:
            Total profit or loss (including premium and intrinsic value)

        Formula:
            P&L = premium_paid + (quantity * intrinsic_value)

        Note:
            - For long positions: intrinsic_value - premium_paid
            - For short positions: premium_received - intrinsic_value
        """
        intrinsic = self.quantity * self.contract.intrinsic_value(price)
        return self.premium_paid + intrinsic


class Strategy(BaseModel):
    """
    Complete trading strategy with stock and option positions.

    Attributes:
        stock: Underlying stock
        stock_quantity: Number of shares held (can be 0)
        option_positions: List of option positions
    """

    stock: Stock
    stock_quantity: int = 0
    option_positions: list[OptionPosition] = Field(default_factory=list)

    def total_payoff(
        self, price: float, include_transaction_costs: bool = True
    ) -> float:
        """
        Calculate total P&L across all positions.

        Args:
            price: Underlying stock price at expiration
            include_transaction_costs: Whether to include commission costs

        Returns:
            Total profit or loss in dollars
        """
        # Stock P&L
        stock_pnl = self.stock_quantity * (price - self.stock.current_price)

        # Options P&L
        options_pnl = sum(pos.payoff_at_price(price) for pos in self.option_positions)

        # Transaction costs
        costs = self.transaction_costs if include_transaction_costs else 0.0

        return stock_pnl + options_pnl - costs

    @property
    def net_premium(self) -> float:
        """
        Total upfront cost (negative) or credit (positive) from options.

        Returns:
            Sum of all option premiums
        """
        return sum(pos.premium_paid for pos in self.option_positions)

    @property
    def transaction_costs(self) -> float:
        """
        Total commission costs for all option positions.

        Returns:
            Total commission in dollars

        Note:
            Uses DEFAULT_TRANSACTION_COST from settings (typically $0.65 per contract)
        """
        from ..config import get_settings

        cost_per_contract = get_settings().default_transaction_cost
        return sum(abs(pos.quantity) * cost_per_contract for pos in self.option_positions)

    def get_earliest_expiration(self) -> date | None:
        """
        Get the earliest expiration date from all option positions.

        Returns:
            Earliest expiration date, or None if no options
        """
        if not self.option_positions:
            return None
        return min(pos.contract.expiration for pos in self.option_positions)

    def validate_single_expiration(self) -> bool:
        """
        Check that all options have the same expiration date.

        Returns:
            True if all options expire on the same date (or no options)
            False if options have different expiration dates

        Note:
            Required for current implementation. Mixed expiration support
            is a future enhancement.
        """
        if not self.option_positions:
            return True
        expirations = {pos.contract.expiration for pos in self.option_positions}
        return len(expirations) == 1

    def validate_for_analysis(self) -> None:
        """
        Validate that strategy is ready for analysis.

        Raises:
            MixedExpirationError: If options have different expiration dates
            MissingBidAskError: If any option lacks bid/ask price data

        Note:
            Call this before performing expensive calculations like Monte Carlo
            simulations to fail fast with clear error messages.
        """
        if not self.option_positions and self.stock_quantity == 0:
            raise ValidationError(
                "No positions in strategy. Add at least one option position or a stock position before analyzing.",
                code="NO_POSITIONS",
            )

        if not self.validate_single_expiration():
            raise MixedExpirationError()

        for pos in self.option_positions:
            if pos.contract.bid is None or pos.contract.ask is None:
                raise MissingBidAskError(
                    f"Missing price data for contract {pos.contract.conid}"
                )
