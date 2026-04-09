"""
Unit tests for strategy optimizer.

Tests cover StrategyResult model, describe_strategy, and optimize_strategies
across all enumeration modes and constraint filters.
Issue: option-9zu
"""

from datetime import date

import pytest

from option_analyzer.models.domain import (
    OptionChain,
    OptionContract,
    OptionPosition,
    Stock,
    Strategy,
)
from option_analyzer.services.risk import RiskMetrics
from option_analyzer.services.statistics import PriceBin

# These imports will fail until the optimizer is implemented (TDD)
from option_analyzer.services.optimizer import (
    StrategyResult,
    describe_strategy,
    optimize_strategies,
)


EXP_DATE = date(2026, 6, 19)
STRIKES = [480.0, 490.0, 500.0, 510.0, 520.0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stock() -> Stock:
    return Stock(symbol="SPY", current_price=500.0, conid=756733)


def _call(strike: float, conid: int, bid: float | None = 5.0, ask: float | None = 5.5) -> OptionContract:
    return OptionContract(conid=conid, strike=strike, right="C", expiration=EXP_DATE, bid=bid, ask=ask)


def _put(strike: float, conid: int, bid: float | None = 5.0, ask: float | None = 5.5) -> OptionContract:
    return OptionContract(conid=conid, strike=strike, right="P", expiration=EXP_DATE, bid=bid, ask=ask)


@pytest.fixture
def chain() -> OptionChain:
    """5 calls + 5 puts at 480/490/500/510/520, all with bid=5.0 ask=5.5."""
    calls = [_call(s, 1000 + i) for i, s in enumerate(STRIKES)]
    puts  = [_put(s,  2000 + i) for i, s in enumerate(STRIKES)]
    return OptionChain(expiration=EXP_DATE, calls=calls, puts=puts)


@pytest.fixture
def sparse_chain() -> OptionChain:
    """Chain with some contracts missing bid or ask."""
    calls = [
        _call(490.0, 1001, bid=None, ask=5.0),  # bid missing → can't short
        _call(500.0, 1002, bid=5.0, ask=None),  # ask missing → can't long
        _call(510.0, 1003, bid=5.0, ask=5.5),   # valid
    ]
    puts = [
        _put(490.0, 2001),
        _put(500.0, 2002),
        _put(510.0, 2003),
    ]
    return OptionChain(expiration=EXP_DATE, calls=calls, puts=puts)


@pytest.fixture
def flat_bins() -> list[PriceBin]:
    """Uniform distribution 480–520, 40 bins of width 1, each count=100."""
    return [PriceBin(lower=480.0 + i, upper=481.0 + i, count=100) for i in range(40)]


# ---------------------------------------------------------------------------
# StrategyResult model
# ---------------------------------------------------------------------------


class TestStrategyResult:
    def test_construction(self, stock: Stock, chain: OptionChain) -> None:
        pos = OptionPosition(contract=chain.calls[2], quantity=1)
        strategy = Strategy(stock=stock, option_positions=[pos])
        metrics = RiskMetrics(
            expected_value=50.0,
            probability_of_profit=0.55,
            max_gain=500.0,
            max_loss=-250.0,
        )
        result = StrategyResult(
            strategy=strategy,
            metrics=metrics,
            ev_to_risk=0.2,
            loss_unbounded=False,
            description="Long Call 500",
        )
        assert result.ev_to_risk == 0.2
        assert result.loss_unbounded is False
        assert result.description == "Long Call 500"


# ---------------------------------------------------------------------------
# describe_strategy
# ---------------------------------------------------------------------------


class TestDescribeStrategy:
    # --- single leg ---

    def test_long_call(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[OptionPosition(contract=chain.calls[2], quantity=1)])
        assert describe_strategy(s) == "Long Call 500"

    def test_short_call(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[OptionPosition(contract=chain.calls[2], quantity=-1)])
        assert describe_strategy(s) == "Short Call 500"

    def test_long_put(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[OptionPosition(contract=chain.puts[2], quantity=1)])
        assert describe_strategy(s) == "Long Put 500"

    def test_short_put(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[OptionPosition(contract=chain.puts[2], quantity=-1)])
        assert describe_strategy(s) == "Short Put 500"

    # --- 2-leg same-right ---

    def test_bull_call_spread(self, stock: Stock, chain: OptionChain) -> None:
        # long lower + short upper call → bull
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.calls[1], quantity=1),   # 490 long
            OptionPosition(contract=chain.calls[3], quantity=-1),  # 510 short
        ])
        assert describe_strategy(s) == "Bull Call Spread 490/510"

    def test_bear_call_spread(self, stock: Stock, chain: OptionChain) -> None:
        # short lower + long upper call → bear
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.calls[1], quantity=-1),  # 490 short
            OptionPosition(contract=chain.calls[3], quantity=1),   # 510 long
        ])
        assert describe_strategy(s) == "Bear Call Spread 490/510"

    def test_bull_put_spread(self, stock: Stock, chain: OptionChain) -> None:
        # long lower + short upper put → bull (credit spread)
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[1], quantity=1),    # 490 long
            OptionPosition(contract=chain.puts[3], quantity=-1),   # 510 short
        ])
        assert describe_strategy(s) == "Bull Put Spread 490/510"

    def test_bear_put_spread(self, stock: Stock, chain: OptionChain) -> None:
        # short lower + long upper put → bear
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[1], quantity=-1),   # 490 short
            OptionPosition(contract=chain.puts[3], quantity=1),    # 510 long
        ])
        assert describe_strategy(s) == "Bear Put Spread 490/510"

    # --- 2-leg mixed-right same-sign (straddle / strangle) ---

    def test_long_straddle(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.calls[2], quantity=1),  # 500 call
            OptionPosition(contract=chain.puts[2], quantity=1),   # 500 put
        ])
        assert describe_strategy(s) == "Long Straddle 500"

    def test_short_straddle(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.calls[2], quantity=-1),
            OptionPosition(contract=chain.puts[2], quantity=-1),
        ])
        assert describe_strategy(s) == "Short Straddle 500"

    def test_long_strangle(self, stock: Stock, chain: OptionChain) -> None:
        # long OTM put + long OTM call at different strikes
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[1],  quantity=1),  # 490 put
            OptionPosition(contract=chain.calls[3], quantity=1),  # 510 call
        ])
        assert describe_strategy(s) == "Long Strangle 490/510"

    def test_short_strangle(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[1],  quantity=-1),
            OptionPosition(contract=chain.calls[3], quantity=-1),
        ])
        assert describe_strategy(s) == "Short Strangle 490/510"

    # --- 2-leg mixed-right mixed-sign (risk reversal family) ---

    def test_risk_reversal(self, stock: Stock, chain: OptionChain) -> None:
        # long call + short put → bullish synthetic
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[1],  quantity=-1),  # 490 put short
            OptionPosition(contract=chain.calls[3], quantity=1),   # 510 call long
        ])
        assert describe_strategy(s) == "Risk Reversal 490/510"

    def test_synthetic_short(self, stock: Stock, chain: OptionChain) -> None:
        # short call + long put → bearish synthetic
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[1],  quantity=1),   # 490 put long
            OptionPosition(contract=chain.calls[3], quantity=-1),  # 510 call short
        ])
        assert describe_strategy(s) == "Synthetic Short 490/510"

    # --- 3-leg butterflies ---

    def test_call_butterfly(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.calls[0], quantity=1),   # 480
            OptionPosition(contract=chain.calls[2], quantity=-2),  # 500 ×2
            OptionPosition(contract=chain.calls[4], quantity=1),   # 520
        ])
        assert describe_strategy(s) == "Call Butterfly 480/500/520"

    def test_put_butterfly(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[0], quantity=1),
            OptionPosition(contract=chain.puts[2], quantity=-2),
            OptionPosition(contract=chain.puts[4], quantity=1),
        ])
        assert describe_strategy(s) == "Put Butterfly 480/500/520"

    # --- 4-leg named ---

    def test_iron_condor(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[0],  quantity=-1),  # 480 short put
            OptionPosition(contract=chain.puts[1],  quantity=1),   # 490 long put
            OptionPosition(contract=chain.calls[3], quantity=1),   # 510 long call
            OptionPosition(contract=chain.calls[4], quantity=-1),  # 520 short call
        ])
        assert describe_strategy(s) == "Iron Condor 480/490/510/520"

    def test_iron_butterfly(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.puts[1],  quantity=1),   # 490 long put
            OptionPosition(contract=chain.puts[2],  quantity=-1),  # 500 short put
            OptionPosition(contract=chain.calls[2], quantity=-1),  # 500 short call
            OptionPosition(contract=chain.calls[3], quantity=1),   # 510 long call
        ])
        assert describe_strategy(s) == "Iron Butterfly 490/500/510"

    # --- stock-based ---

    def test_long_stock(self, stock: Stock) -> None:
        s = Strategy(stock=stock, stock_quantity=100, option_positions=[])
        assert describe_strategy(s) == "Long Stock"

    def test_short_stock(self, stock: Stock) -> None:
        s = Strategy(stock=stock, stock_quantity=-100, option_positions=[])
        assert describe_strategy(s) == "Short Stock"

    def test_covered_call(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, stock_quantity=100, option_positions=[
            OptionPosition(contract=chain.calls[3], quantity=-1),  # 510 short call
        ])
        assert describe_strategy(s) == "Covered Call 510"

    def test_protective_put(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, stock_quantity=100, option_positions=[
            OptionPosition(contract=chain.puts[1], quantity=1),  # 490 long put
        ])
        assert describe_strategy(s) == "Protective Put 490"

    def test_collar(self, stock: Stock, chain: OptionChain) -> None:
        s = Strategy(stock=stock, stock_quantity=100, option_positions=[
            OptionPosition(contract=chain.puts[1],  quantity=1),   # 490 long put
            OptionPosition(contract=chain.calls[3], quantity=-1),  # 510 short call
        ])
        assert describe_strategy(s) == "Collar 490/510"

    # --- fallback ---

    def test_fallback_description(self, stock: Stock, chain: OptionChain) -> None:
        # Non-standard 3-leg (e.g. ratio spread): doesn't match any named pattern
        s = Strategy(stock=stock, option_positions=[
            OptionPosition(contract=chain.calls[1], quantity=1),
            OptionPosition(contract=chain.calls[2], quantity=1),
            OptionPosition(contract=chain.calls[3], quantity=-3),
        ])
        desc = describe_strategy(s)
        # Just verify it returns something non-empty and doesn't crash
        assert isinstance(desc, str) and len(desc) > 0


# ---------------------------------------------------------------------------
# 1-leg enumeration
# ---------------------------------------------------------------------------


class TestOptimizeStrategies1Leg:
    def _run(self, stock, chain, flat_bins, **kw):
        return optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=True,
            include_2leg=False,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=100,
            **kw,
        )

    def test_count(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """5 calls + 5 puts × 2 directions = 20 single-leg strategies."""
        results = self._run(stock, chain, flat_bins)
        assert len(results) == 20

    def test_each_has_one_option_position(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        assert all(len(r.strategy.option_positions) == 1 for r in results)

    def test_no_stock_quantity(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        assert all(r.strategy.stock_quantity == 0 for r in results)

    def test_skips_long_when_ask_missing(self, stock: Stock, sparse_chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Long direction is skipped for contracts with ask=None."""
        results = self._run(stock, sparse_chain, flat_bins)
        long_conids = {
            r.strategy.option_positions[0].contract.conid
            for r in results
            if r.strategy.option_positions[0].quantity > 0
        }
        assert 1002 not in long_conids  # 500 call: ask=None

    def test_skips_short_when_bid_missing(self, stock: Stock, sparse_chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Short direction is skipped for contracts with bid=None."""
        results = self._run(stock, sparse_chain, flat_bins)
        short_conids = {
            r.strategy.option_positions[0].contract.conid
            for r in results
            if r.strategy.option_positions[0].quantity < 0
        }
        assert 1001 not in short_conids  # 490 call: bid=None


# ---------------------------------------------------------------------------
# 2-leg exhaustive enumeration
# ---------------------------------------------------------------------------


class TestOptimizeStrategies2Leg:
    def _run(self, stock, chain, flat_bins, **kw):
        return optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=False,
            include_2leg=True,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=200,
            **kw,
        )

    def test_count(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """C(10, 2) = 45 pairs × 2 directions = 90 two-leg strategies."""
        results = self._run(stock, chain, flat_bins)
        assert len(results) == 90

    def test_each_has_two_option_positions(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        assert all(len(r.strategy.option_positions) == 2 for r in results)

    def test_both_polarities_for_a_pair(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """For a given pair (A, B), both (long A + short B) and (short A + long B) appear."""
        results = self._run(stock, chain, flat_bins)
        c1_id, c2_id = chain.calls[0].conid, chain.calls[1].conid
        found_pos = False
        found_neg = False
        for r in results:
            by_conid = {p.contract.conid: p.quantity for p in r.strategy.option_positions}
            if c1_id in by_conid and c2_id in by_conid:
                if by_conid[c1_id] > 0 and by_conid[c2_id] < 0:
                    found_pos = True
                elif by_conid[c1_id] < 0 and by_conid[c2_id] > 0:
                    found_neg = True
        assert found_pos and found_neg

    def test_no_duplicate_contract_in_strategy(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        for r in results:
            conids = [p.contract.conid for p in r.strategy.option_positions]
            assert len(set(conids)) == len(conids)


# ---------------------------------------------------------------------------
# Named multi-leg enumeration
# ---------------------------------------------------------------------------


class TestOptimizeStrategiesNamedMultileg:
    def _run(self, stock, chain, flat_bins, **kw):
        return optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=False,
            include_2leg=False,
            include_named_multileg=True,
            include_stock_legs=False,
            top_n=500,
            **kw,
        )

    def test_produces_results(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        assert len(results) > 0

    def test_iron_condor_strike_ordering(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        condors = [r for r in results if "Iron Condor" in r.description]
        assert len(condors) > 0
        for r in condors:
            puts  = sorted([p for p in r.strategy.option_positions if p.contract.right == "P"], key=lambda p: p.contract.strike)
            calls = sorted([p for p in r.strategy.option_positions if p.contract.right == "C"], key=lambda p: p.contract.strike)
            assert len(puts) == 2 and len(calls) == 2
            # Inner put strike < inner call strike (gap between wings)
            assert puts[1].contract.strike < calls[0].contract.strike
            # Outer legs are long, inner legs are short
            assert puts[0].quantity == -1   # outer short put
            assert puts[1].quantity == 1    # inner long put
            assert calls[0].quantity == 1   # inner long call
            assert calls[1].quantity == -1  # outer short call

    def test_call_butterfly_middle_quantity(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        butterflies = [r for r in results if "Call Butterfly" in r.description]
        assert len(butterflies) > 0
        for r in butterflies:
            positions = sorted(r.strategy.option_positions, key=lambda p: p.contract.strike)
            assert positions[1].quantity == -2  # middle leg is short ×2

    def test_put_butterfly_middle_quantity(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        butterflies = [r for r in results if "Put Butterfly" in r.description]
        assert len(butterflies) > 0
        for r in butterflies:
            positions = sorted(r.strategy.option_positions, key=lambda p: p.contract.strike)
            assert positions[1].quantity == -2

    def test_iron_butterfly_shared_atm_strike(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        iron_bflies = [r for r in results if "Iron Butterfly" in r.description]
        assert len(iron_bflies) > 0
        for r in iron_bflies:
            short_legs = [p for p in r.strategy.option_positions if p.quantity < 0]
            assert len(short_legs) == 2
            # The two short legs must share the same strike (ATM)
            short_strikes = {p.contract.strike for p in short_legs}
            assert len(short_strikes) == 1

    def test_skips_missing_bid_on_short_leg(self, stock: Stock, sparse_chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Named strategies never use a contract with bid=None as a short leg."""
        results = self._run(stock, sparse_chain, flat_bins)
        for r in results:
            for p in r.strategy.option_positions:
                if p.contract.conid == 1001:  # bid=None
                    assert p.quantity > 0  # only allowed as long

    def test_skips_missing_ask_on_long_leg(self, stock: Stock, sparse_chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Named strategies never use a contract with ask=None as a long leg."""
        results = self._run(stock, sparse_chain, flat_bins)
        for r in results:
            for p in r.strategy.option_positions:
                if p.contract.conid == 1002:  # ask=None
                    assert p.quantity < 0  # only allowed as short


# ---------------------------------------------------------------------------
# Stock + options enumeration
# ---------------------------------------------------------------------------


class TestOptimizeStrategiesStockLegs:
    def _run(self, stock, chain, flat_bins, stock_qty=100, **kw):
        return optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=False,
            include_2leg=False,
            include_named_multileg=False,
            include_stock_legs=True,
            stock_qty=stock_qty,
            top_n=500,
            **kw,
        )

    def test_stock_only_long_and_short_present(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        descriptions = {r.description for r in results}
        assert "Long Stock" in descriptions
        assert "Short Stock" in descriptions

    def test_covered_call_present(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        descriptions = {r.description for r in results}
        assert any("Covered Call" in d for d in descriptions)

    def test_protective_put_present(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        descriptions = {r.description for r in results}
        assert any("Protective Put" in d for d in descriptions)

    def test_collar_present(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins)
        descriptions = {r.description for r in results}
        assert any("Collar" in d for d in descriptions)

    def test_stock_quantity_applied(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = self._run(stock, chain, flat_bins, stock_qty=200)
        stock_positions = [r for r in results if r.strategy.stock_quantity != 0]
        assert all(abs(r.strategy.stock_quantity) == 200 for r in stock_positions)

    def test_no_option_positions_without_include_1leg(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """include_stock_legs without include_1leg/2leg should still produce stock+option combos."""
        results = self._run(stock, chain, flat_bins)
        # There should be some strategies with both stock and options
        with_both = [r for r in results if r.strategy.stock_quantity != 0 and len(r.strategy.option_positions) > 0]
        assert len(with_both) > 0


# ---------------------------------------------------------------------------
# EV-to-risk scoring and loss_unbounded flag
# ---------------------------------------------------------------------------


class TestEvToRisk:
    def test_sorted_by_ev_to_risk_descending(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(flat_bins, chain, stock, top_n=100)
        ratios = [r.ev_to_risk for r in results]
        assert ratios == sorted(ratios, reverse=True)

    def test_all_ev_to_risk_finite(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(flat_bins, chain, stock, top_n=200)
        assert all(abs(r.ev_to_risk) < float("inf") for r in results)

    def test_defined_risk_ratio_correct(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """ev_to_risk == EV / abs(effective_max_loss) for a defined-risk strategy.

        effective_max_loss = min(mc_max_loss, theoretical_max_loss) — the more
        conservative of the two, since the MC range may not cover the full loss zone.
        """
        from option_analyzer.services.optimizer import theoretical_max_loss as theo_ml
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=False,
            include_2leg=True,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=200,
        )
        for r in results:
            if r.loss_unbounded or r.metrics.max_loss is None:
                continue
            effective = min(r.metrics.max_loss, theo_ml(r.strategy))
            if effective < -1e-2:
                expected = r.metrics.expected_value / abs(effective)
                assert abs(r.ev_to_risk - expected) < 1e-9
                break
        else:
            pytest.fail("No defined-risk 2-leg strategy found to verify ratio")

    def test_naked_short_call_is_unbounded(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Naked short call has no finite theoretical loss ceiling → loss_unbounded=True."""
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=True,
            include_2leg=False,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=100,
        )
        short_calls = [
            r for r in results
            if r.strategy.option_positions[0].contract.right == "C"
            and r.strategy.option_positions[0].quantity < 0
        ]
        assert len(short_calls) > 0
        assert all(r.loss_unbounded is True for r in short_calls)

    def test_long_call_not_unbounded(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Long call max loss is the premium → bounded."""
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=True,
            include_2leg=False,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=100,
        )
        long_calls = [
            r for r in results
            if r.strategy.option_positions[0].contract.right == "C"
            and r.strategy.option_positions[0].quantity > 0
        ]
        assert len(long_calls) > 0
        assert all(r.loss_unbounded is False for r in long_calls)

    def test_short_put_not_unbounded(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Short put loss is bounded by strike (stock can't go below 0)."""
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=True,
            include_2leg=False,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=100,
        )
        short_puts = [
            r for r in results
            if r.strategy.option_positions[0].contract.right == "P"
            and r.strategy.option_positions[0].quantity < 0
        ]
        assert len(short_puts) > 0
        assert all(r.loss_unbounded is False for r in short_puts)

    def test_bull_call_spread_not_unbounded(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Hedged call spread has defined max loss."""
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=False,
            include_2leg=True,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=200,
        )
        spreads = [r for r in results if "Bull Call Spread" in r.description]
        assert len(spreads) > 0
        assert all(r.loss_unbounded is False for r in spreads)


# ---------------------------------------------------------------------------
# Constraint filters and integration
# ---------------------------------------------------------------------------


class TestOptimizeStrategiesFilters:
    def test_top_n_respected(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(flat_bins, chain, stock, top_n=5)
        assert len(results) <= 5

    def test_max_loss_limit_applied(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        """Strategies with max_loss worse than -limit are excluded."""
        results = optimize_strategies(
            flat_bins, chain, stock,
            max_loss_limit=50.0,
            top_n=500,
        )
        for r in results:
            if r.metrics.max_loss is not None:
                assert r.metrics.max_loss >= -50.0

    def test_min_pop_applied(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(
            flat_bins, chain, stock,
            min_pop=0.5,
            top_n=200,
        )
        assert all(r.metrics.probability_of_profit >= 0.5 for r in results)

    def test_min_ev_applied(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(
            flat_bins, chain, stock,
            min_ev=0.0,
            top_n=200,
        )
        assert all(r.metrics.expected_value >= 0.0 for r in results)

    def test_include_1leg_false_excludes_single_legs(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=False,
            include_stock_legs=False,
            top_n=500,
        )
        single_leg_pure = [
            r for r in results
            if len(r.strategy.option_positions) == 1 and r.strategy.stock_quantity == 0
        ]
        assert len(single_leg_pure) == 0

    def test_include_2leg_false_excludes_pairs(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=True,
            include_2leg=False,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=500,
        )
        two_leg_pure = [
            r for r in results
            if len(r.strategy.option_positions) == 2 and r.strategy.stock_quantity == 0
        ]
        assert len(two_leg_pure) == 0

    def test_include_named_multileg_false(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_1leg=False,
            include_2leg=False,
            include_named_multileg=False,
            include_stock_legs=False,
            top_n=500,
        )
        assert results == []

    def test_include_stock_legs_false_no_stock(self, stock: Stock, chain: OptionChain, flat_bins: list[PriceBin]) -> None:
        results = optimize_strategies(
            flat_bins, chain, stock,
            include_stock_legs=False,
            top_n=500,
        )
        assert all(r.strategy.stock_quantity == 0 for r in results)

    def test_empty_chain_returns_empty(self, stock: Stock, flat_bins: list[PriceBin]) -> None:
        """No options in chain → no option-based strategies (stock-only disabled)."""
        empty = OptionChain(expiration=EXP_DATE, calls=[], puts=[])
        results = optimize_strategies(
            flat_bins, empty, stock,
            include_stock_legs=False,
            top_n=100,
        )
        assert results == []

    def test_empty_bins_raises(self, stock: Stock, chain: OptionChain) -> None:
        with pytest.raises(ValueError, match="bins"):
            optimize_strategies([], chain, stock)
