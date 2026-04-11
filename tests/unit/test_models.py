"""
Unit tests for domain models.

Tests cover:
- Stock payoff calculations
- OptionContract intrinsic value and days to expiry
- OptionPosition premium and payoff calculations
- Strategy validation and P&L calculations
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from option_analyzer.models.domain import (
    OptionChain,
    OptionContract,
    OptionPosition,
    Stock,
    Strategy,
)
from option_analyzer.utils.exceptions import (
    InvalidQuantityError,
    MissingBidAskError,
    MixedExpirationError,
)
from option_analyzer.utils.exceptions import (
    ValidationError as AppValidationError,
)


class TestStock:
    """Test Stock model."""

    def test_stock_creation(self, sample_stock: Stock) -> None:
        """Test basic stock creation."""
        assert sample_stock.symbol == "AAPL"
        assert sample_stock.current_price == 100.0
        assert sample_stock.conid == 265598
        assert len(sample_stock.available_expirations) == 3

    def test_stock_payoff_at_price(self, sample_stock: Stock) -> None:
        """Test stock payoff calculation."""
        # Above current price
        assert sample_stock.payoff_at_price(110.0) == 10.0
        # At current price
        assert sample_stock.payoff_at_price(100.0) == 0.0
        # Below current price
        assert sample_stock.payoff_at_price(90.0) == -10.0

    def test_stock_requires_positive_price(self) -> None:
        """Test that stock price must be positive."""
        with pytest.raises(ValidationError):
            Stock(symbol="AAPL", current_price=-10.0, conid=265598)

        with pytest.raises(ValidationError):
            Stock(symbol="AAPL", current_price=0.0, conid=265598)


class TestOptionContract:
    """Test OptionContract model."""

    def test_call_intrinsic_value(self, sample_call: OptionContract) -> None:
        """Test call option intrinsic value calculation."""
        # Below strike: worthless
        assert sample_call.intrinsic_value(95.0) == 0.0
        # At strike: worthless
        assert sample_call.intrinsic_value(100.0) == 0.0
        # Above strike: (price - strike) * multiplier
        assert sample_call.intrinsic_value(110.0) == 1000.0  # (110 - 100) * 100

    def test_put_intrinsic_value(self, sample_put: OptionContract) -> None:
        """Test put option intrinsic value calculation."""
        # Above strike: worthless
        assert sample_put.intrinsic_value(105.0) == 0.0
        # At strike: worthless
        assert sample_put.intrinsic_value(100.0) == 0.0
        # Below strike: (strike - price) * multiplier
        assert sample_put.intrinsic_value(90.0) == 1000.0  # (100 - 90) * 100

    def test_days_to_expiry(self) -> None:
        """Test days to expiry calculation."""
        future = date.today() + timedelta(days=30)
        contract = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=future,
            bid=5.0,
            ask=5.5,
        )

        # Default reference date (today)
        assert contract.days_to_expiry() == 30

        # Custom reference date
        ref = date.today() + timedelta(days=10)
        assert contract.days_to_expiry(ref) == 20

    def test_days_to_expiry_raises_for_expired_option(self) -> None:
        """Test that days_to_expiry raises ValueError for expired options."""
        past = date.today() - timedelta(days=10)
        contract = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=past,
            bid=5.0,
            ask=5.5,
        )

        # Should raise ValueError when option is expired (default to today)
        with pytest.raises(ValueError, match="Option has expired"):
            contract.days_to_expiry()

        # Should raise ValueError with custom reference date after expiration
        future_ref = date.today() + timedelta(days=5)
        with pytest.raises(ValueError, match="Option has expired"):
            contract.days_to_expiry(future_ref)

    def test_option_requires_positive_strike(self) -> None:
        """Test that strike must be positive."""
        with pytest.raises(ValidationError):
            OptionContract(
                conid=12345,
                strike=-100.0,
                right="C",
                expiration=date(2024, 12, 20),
            )


class TestOptionChain:
    """Test OptionChain model."""

    def test_option_chain_creation(self, sample_call: OptionContract, sample_put: OptionContract) -> None:
        """Test basic option chain creation."""
        chain = OptionChain(
            expiration=date(2024, 12, 20),
            calls=[sample_call],
            puts=[sample_put],
        )

        assert chain.expiration == date(2024, 12, 20)
        assert len(chain.calls) == 1
        assert len(chain.puts) == 1
        assert chain.calls[0] == sample_call
        assert chain.puts[0] == sample_put

    def test_option_chain_without_underlying_price(self, sample_call: OptionContract) -> None:
        """Test chain without underlying price."""
        chain = OptionChain(
            expiration=date(2024, 12, 20),
            calls=[sample_call],
            puts=[],
        )

        assert len(chain.calls) == 1
        assert len(chain.puts) == 0

    def test_option_chain_empty_lists(self) -> None:
        """Test chain with no options."""
        chain = OptionChain(
            expiration=date(2024, 12, 20),
        )

        assert chain.calls == []
        assert chain.puts == []

    def test_option_chain_with_multiple_options(self) -> None:
        """Test chain with multiple calls and puts."""
        call1 = OptionContract(
            conid=12345,
            strike=95.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=7.0,
            ask=7.5,
        )
        call2 = OptionContract(
            conid=12346,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=5.0,
            ask=5.5,
        )
        put1 = OptionContract(
            conid=12347,
            strike=95.0,
            right="P",
            expiration=date(2024, 12, 20),
            bid=2.5,
            ask=3.0,
        )
        put2 = OptionContract(
            conid=12348,
            strike=100.0,
            right="P",
            expiration=date(2024, 12, 20),
            bid=3.0,
            ask=3.5,
        )

        chain = OptionChain(
            expiration=date(2024, 12, 20),
            calls=[call1, call2],
            puts=[put1, put2],
        )

        assert len(chain.calls) == 2
        assert len(chain.puts) == 2
        assert chain.calls[0].strike == 95.0
        assert chain.calls[1].strike == 100.0
        assert chain.puts[0].strike == 95.0
        assert chain.puts[1].strike == 100.0


class TestOptionPosition:
    """Test OptionPosition model."""

    def test_long_call_premium(self, sample_call: OptionContract) -> None:
        """Test premium calculation for long call."""
        pos = OptionPosition(contract=sample_call, quantity=1)
        # Long call pays ask: -1 * 5.5 * 100 = -550
        assert pos.premium_paid == -550.0

    def test_short_call_premium(self, sample_call: OptionContract) -> None:
        """Test premium calculation for short call."""
        pos = OptionPosition(contract=sample_call, quantity=-1)
        # Short call receives bid: -(-1) * 5.0 * 100 = 500
        assert pos.premium_paid == 500.0

    def test_long_put_premium(self, sample_put: OptionContract) -> None:
        """Test premium calculation for long put."""
        pos = OptionPosition(contract=sample_put, quantity=1)
        # Long put pays ask: -1 * 3.5 * 100 = -350
        assert pos.premium_paid == -350.0

    def test_short_put_premium(self, sample_put: OptionContract) -> None:
        """Test premium calculation for short put."""
        pos = OptionPosition(contract=sample_put, quantity=-1)
        # Short put receives bid: -(-1) * 3.0 * 100 = 300
        assert pos.premium_paid == 300.0

    def test_long_call_payoff(self, sample_call: OptionContract) -> None:
        """Test P&L calculation for long call."""
        pos = OptionPosition(contract=sample_call, quantity=1)

        # Below strike: lose premium
        assert pos.payoff_at_price(95.0) == -550.0

        # At strike: lose premium
        assert pos.payoff_at_price(100.0) == -550.0

        # Above strike: intrinsic - premium
        # Intrinsic: (110 - 100) * 100 = 1000
        # Premium: -550
        # Total: 1000 - 550 = 450
        assert pos.payoff_at_price(110.0) == 450.0

    def test_short_put_payoff(self, sample_put: OptionContract) -> None:
        """Test P&L calculation for short put."""
        pos = OptionPosition(contract=sample_put, quantity=-1)

        # Above strike: keep premium
        assert pos.payoff_at_price(105.0) == 300.0

        # Below strike: intrinsic loss + premium
        # Premium received: 300
        # Intrinsic: -(100 - 90) * 100 = -1000
        # Total: 300 - 1000 = -700
        assert pos.payoff_at_price(90.0) == -700.0

    def test_zero_quantity_rejected(self, sample_call: OptionContract) -> None:
        """Test that zero quantity is rejected."""
        with pytest.raises(InvalidQuantityError):
            OptionPosition(contract=sample_call, quantity=0)

    def test_missing_bid_ask_handling(self) -> None:
        """Test that missing bid/ask prices raise MissingBidAskError."""
        contract = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=None,
            ask=None,
        )
        # Long position: should raise error when ask is unavailable
        pos_long = OptionPosition(contract=contract, quantity=1)
        with pytest.raises(MissingBidAskError, match="Missing ask price"):
            _ = pos_long.premium_paid

        # Short position: should raise error when bid is unavailable
        pos_short = OptionPosition(contract=contract, quantity=-1)
        with pytest.raises(MissingBidAskError, match="Missing bid price"):
            _ = pos_short.premium_paid


class TestStrategy:
    """Test Strategy model."""

    def test_covered_call(self, sample_stock: Stock, sample_call: OptionContract) -> None:
        """Test covered call strategy P&L."""
        strategy = Strategy(
            stock=sample_stock,
            stock_quantity=100,
            option_positions=[
                OptionPosition(contract=sample_call, quantity=-1)  # Short call
            ],
        )

        # At price of 110:
        # - Stock gains: 100 shares * (110 - 100) = 1000
        # - Call intrinsic: -(110 - 100) * 100 = -1000
        # - Call premium received: 5.0 * 100 = 500
        # - Transaction cost: 1 contract * 0.65 = 0.65
        # - Net: 1000 - 1000 + 500 - 0.65 = 499.35
        assert strategy.total_payoff(110.0) == 499.35

    def test_long_straddle(
        self, sample_stock: Stock, sample_call: OptionContract, sample_put: OptionContract
    ) -> None:
        """Test long straddle strategy."""
        strategy = Strategy(
            stock=sample_stock,
            stock_quantity=0,
            option_positions=[
                OptionPosition(contract=sample_call, quantity=1),  # Long call
                OptionPosition(contract=sample_put, quantity=1),  # Long put
            ],
        )

        # At price of 120:
        # - Call P&L: (120 - 100) * 100 - 550 = 1450
        # - Put P&L: 0 - 350 = -350
        # - Transaction costs: 2 * 0.65 = 1.30
        # - Net: 1450 - 350 - 1.30 = 1098.70
        assert strategy.total_payoff(120.0) == 1098.70

        # At price of 80:
        # - Call P&L: 0 - 550 = -550
        # - Put P&L: (100 - 80) * 100 - 350 = 1650
        # - Transaction costs: 2 * 0.65 = 1.30
        # - Net: -550 + 1650 - 1.30 = 1098.70
        assert strategy.total_payoff(80.0) == 1098.70

    def test_net_premium(self, sample_stock: Stock, sample_call: OptionContract) -> None:
        """Test net premium calculation."""
        strategy = Strategy(
            stock=sample_stock,
            stock_quantity=0,
            option_positions=[
                OptionPosition(contract=sample_call, quantity=-2)  # Short 2 calls
            ],
        )
        # Short 2 calls at bid of 5.0: 2 * 5.0 * 100 = 1000
        assert strategy.net_premium == 1000.0

    def test_transaction_costs(
        self, sample_stock: Stock, sample_call: OptionContract, sample_put: OptionContract
    ) -> None:
        """Test transaction cost calculation."""
        strategy = Strategy(
            stock=sample_stock,
            stock_quantity=0,
            option_positions=[
                OptionPosition(contract=sample_call, quantity=1),
                OptionPosition(contract=sample_put, quantity=-2),
            ],
        )
        # 3 total contracts at $0.65 each = $1.95
        assert strategy.transaction_costs == pytest.approx(1.95)

    def test_get_earliest_expiration(self, sample_stock: Stock) -> None:
        """Test earliest expiration calculation."""
        early_call = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=5.0,
            ask=5.5,
        )
        late_call = OptionContract(
            conid=12346,
            strike=105.0,
            right="C",
            expiration=date(2025, 1, 17),
            bid=3.0,
            ask=3.5,
        )

        strategy = Strategy(
            stock=sample_stock,
            option_positions=[
                OptionPosition(contract=late_call, quantity=1),
                OptionPosition(contract=early_call, quantity=-1),
            ],
        )

        assert strategy.get_earliest_expiration() == date(2024, 12, 20)

    def test_earliest_expiration_empty(self, sample_stock: Stock) -> None:
        """Test earliest expiration with no options."""
        strategy = Strategy(stock=sample_stock, option_positions=[])
        assert strategy.get_earliest_expiration() is None

    def test_validate_single_expiration_pass(self, sample_stock: Stock) -> None:
        """Test single expiration validation passes."""
        call1 = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=5.0,
            ask=5.5,
        )
        call2 = OptionContract(
            conid=12346,
            strike=105.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=3.0,
            ask=3.5,
        )

        strategy = Strategy(
            stock=sample_stock,
            option_positions=[
                OptionPosition(contract=call1, quantity=1),
                OptionPosition(contract=call2, quantity=-1),
            ],
        )

        assert strategy.validate_single_expiration() is True

    def test_validate_single_expiration_fail(self, sample_stock: Stock) -> None:
        """Test single expiration validation fails with mixed dates."""
        call1 = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=5.0,
            ask=5.5,
        )
        call2 = OptionContract(
            conid=12346,
            strike=105.0,
            right="C",
            expiration=date(2025, 1, 17),
            bid=3.0,
            ask=3.5,
        )

        strategy = Strategy(
            stock=sample_stock,
            option_positions=[
                OptionPosition(contract=call1, quantity=1),
                OptionPosition(contract=call2, quantity=-1),
            ],
        )

        assert strategy.validate_single_expiration() is False

    def test_validate_single_expiration_empty(self, sample_stock: Stock) -> None:
        """Test single expiration validation with no options."""
        strategy = Strategy(stock=sample_stock, option_positions=[])
        assert strategy.validate_single_expiration() is True

    def test_payoff_without_transaction_costs(
        self, sample_stock: Stock, sample_call: OptionContract
    ) -> None:
        """Test payoff calculation without transaction costs."""
        strategy = Strategy(
            stock=sample_stock,
            stock_quantity=0,
            option_positions=[OptionPosition(contract=sample_call, quantity=1)],
        )

        # With costs
        payoff_with_costs = strategy.total_payoff(110.0, include_transaction_costs=True)
        # Without costs
        payoff_without_costs = strategy.total_payoff(110.0, include_transaction_costs=False)

        assert payoff_with_costs == payoff_without_costs - 0.65

    def test_validate_for_analysis_valid_strategy(
        self, sample_stock: Stock, sample_call: OptionContract
    ) -> None:
        """Test that valid strategy passes validation."""
        strategy = Strategy(
            stock=sample_stock,
            stock_quantity=0,
            option_positions=[OptionPosition(contract=sample_call, quantity=1)],
        )
        # Should not raise any exceptions
        strategy.validate_for_analysis()

    def test_validate_for_analysis_empty_strategy(self, sample_stock: Stock) -> None:
        """Test that strategy with no positions at all raises ValidationError."""
        strategy = Strategy(stock=sample_stock, stock_quantity=0, option_positions=[])
        with pytest.raises(AppValidationError, match="No positions"):
            strategy.validate_for_analysis()

    def test_validate_for_analysis_stock_only_is_valid(self, sample_stock: Stock) -> None:
        """Test that a stock-only strategy (no options) passes validation."""
        strategy = Strategy(stock=sample_stock, stock_quantity=100, option_positions=[])
        strategy.validate_for_analysis()  # should not raise

    def test_validate_for_analysis_mixed_expiration(self, sample_stock: Stock) -> None:
        """Test that mixed expiration dates raise MixedExpirationError."""
        call1 = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=5.0,
            ask=5.5,
        )
        call2 = OptionContract(
            conid=12346,
            strike=105.0,
            right="C",
            expiration=date(2025, 1, 17),
            bid=3.0,
            ask=3.5,
        )

        strategy = Strategy(
            stock=sample_stock,
            option_positions=[
                OptionPosition(contract=call1, quantity=1),
                OptionPosition(contract=call2, quantity=-1),
            ],
        )

        with pytest.raises(MixedExpirationError):
            strategy.validate_for_analysis()

    def test_validate_for_analysis_missing_bid(self, sample_stock: Stock) -> None:
        """Test that missing bid raises MissingBidAskError."""
        call = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=None,  # Missing bid
            ask=5.5,
        )

        strategy = Strategy(
            stock=sample_stock,
            option_positions=[OptionPosition(contract=call, quantity=1)],
        )

        with pytest.raises(MissingBidAskError, match="Missing price data for contract 12345"):
            strategy.validate_for_analysis()

    def test_validate_for_analysis_missing_ask(self, sample_stock: Stock) -> None:
        """Test that missing ask raises MissingBidAskError."""
        call = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=5.0,
            ask=None,  # Missing ask
        )

        strategy = Strategy(
            stock=sample_stock,
            option_positions=[OptionPosition(contract=call, quantity=1)],
        )

        with pytest.raises(MissingBidAskError, match="Missing price data for contract 12345"):
            strategy.validate_for_analysis()

    def test_validate_for_analysis_missing_both_bid_ask(self, sample_stock: Stock) -> None:
        """Test that missing both bid and ask raises MissingBidAskError."""
        call = OptionContract(
            conid=12345,
            strike=100.0,
            right="C",
            expiration=date(2024, 12, 20),
            bid=None,  # Missing bid
            ask=None,  # Missing ask
        )

        strategy = Strategy(
            stock=sample_stock,
            option_positions=[OptionPosition(contract=call, quantity=1)],
        )

        with pytest.raises(MissingBidAskError, match="Missing price data for contract 12345"):
            strategy.validate_for_analysis()
