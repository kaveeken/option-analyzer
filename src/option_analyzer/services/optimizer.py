"""
Strategy optimizer: enumerate and rank option strategies by EV-to-risk ratio.

Given a pre-computed Monte Carlo price distribution (list[PriceBin]) and an
OptionChain, this module exhaustively generates candidates across several
strategy families, scores each by EV / abs(max_loss), and returns the top N
sorted results.

Strategy families:
- 1-leg: long/short every call and put in the chain
- 2-leg exhaustive: all pairs of contracts in both directions
- Named multi-leg: iron condor, call/put butterfly, iron butterfly
- Stock + 0–2 option legs: long/short stock combined with option positions

Scoring:
    ev_to_risk = EV / abs(max_loss)

    For strategies with theoretically unbounded upside loss (naked short calls,
    short stock without a long call hedge) the MC-derived max_loss is used as
    the denominator and loss_unbounded is flagged True.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import combinations
from math import comb

from pydantic import BaseModel

from ..models.domain import OptionChain, OptionContract, OptionPosition, Stock, Strategy
from ..services.risk import RiskMetrics, calculate_expected_value, calculate_probability_of_profit
from ..services.statistics import PriceBin


class StrategyResult(BaseModel):
    """Scored strategy candidate from optimize_strategies."""

    strategy: Strategy
    metrics: RiskMetrics
    ev_to_risk: float
    loss_unbounded: bool  # True when payoff has no finite theoretical floor
    description: str


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def describe_strategy(strategy: Strategy) -> str:
    """
    Return a human-readable name for a strategy.

    Detects standard named patterns (bull call spread, iron condor, covered call,
    etc.) and falls back to a generic description for unrecognised structures.
    """
    sq = strategy.stock_quantity
    positions = sorted(strategy.option_positions, key=lambda p: (p.contract.strike, p.contract.right))
    n = len(positions)

    # --- stock only ---
    if n == 0:
        return "Long Stock" if sq > 0 else "Short Stock"

    # --- extract structure ---
    def _strike(p: OptionPosition) -> float:
        return p.contract.strike

    def _right(p: OptionPosition) -> str:
        return p.contract.right

    def _qty(p: OptionPosition) -> int:
        return p.quantity

    # --- stock + option combos ---
    if sq != 0:
        if n == 1:
            p = positions[0]
            if sq > 0 and _right(p) == "C" and _qty(p) < 0:
                return f"Covered Call {int(_strike(p))}"
            if sq > 0 and _right(p) == "P" and _qty(p) > 0:
                return f"Protective Put {int(_strike(p))}"
            if sq < 0 and _right(p) == "P" and _qty(p) < 0:
                return f"Covered Put {int(_strike(p))}"
        if n == 2:
            puts  = [p for p in positions if _right(p) == "P"]
            calls = [p for p in positions if _right(p) == "C"]
            if sq > 0 and len(puts) == 1 and len(calls) == 1:
                lp = puts[0]
                sc = calls[0]
                if _qty(lp) > 0 and _qty(sc) < 0:
                    return f"Collar {int(_strike(lp))}/{int(_strike(sc))}"

    # --- pure option patterns ---

    if n == 1:
        p = positions[0]
        side = "Long" if _qty(p) > 0 else "Short"
        right = "Call" if _right(p) == "C" else "Put"
        return f"{side} {right} {int(_strike(p))}"

    if n == 2:
        p1, p2 = positions[0], positions[1]
        same_right = _right(p1) == _right(p2)
        same_strike = _strike(p1) == _strike(p2)
        same_sign = (_qty(p1) > 0) == (_qty(p2) > 0)

        if same_right:
            # Spreads — lower strike is positions[0] by sort
            kind = "Call" if _right(p1) == "C" else "Put"
            lower_is_long = _qty(p1) > 0
            if kind == "Call":
                label = "Bull" if lower_is_long else "Bear"
            else:
                label = "Bull" if lower_is_long else "Bear"
            return f"{label} {kind} Spread {int(_strike(p1))}/{int(_strike(p2))}"

        # Mixed right
        if same_sign:
            if same_strike:
                side = "Long" if _qty(p1) > 0 else "Short"
                return f"{side} Straddle {int(_strike(p1))}"
            # Different strikes: p1 has lower strike; sort puts before calls within same strike
            put = next(p for p in positions if _right(p) == "P")
            call = next(p for p in positions if _right(p) == "C")
            side = "Long" if _qty(put) > 0 else "Short"
            return f"{side} Strangle {int(_strike(put))}/{int(_strike(call))}"

        # Mixed right, mixed sign
        put = next(p for p in positions if _right(p) == "P")
        call = next(p for p in positions if _right(p) == "C")
        put_k = int(_strike(put))
        call_k = int(_strike(call))
        if _qty(call) > 0 and _qty(put) < 0:
            return f"Risk Reversal {put_k}/{call_k}"
        else:
            return f"Synthetic Short {put_k}/{call_k}"

    if n == 3:
        rights = {_right(p) for p in positions}
        if len(rights) == 1:
            kind = "Call" if "C" in rights else "Put"
            strikes = [int(_strike(p)) for p in positions]  # sorted by strike
            mid = positions[1]
            if _qty(mid) == -2:
                return f"{kind} Butterfly {strikes[0]}/{strikes[1]}/{strikes[2]}"

    if n == 4:
        puts  = sorted([p for p in positions if _right(p) == "P"], key=_strike)
        calls = sorted([p for p in positions if _right(p) == "C"], key=_strike)
        if len(puts) == 2 and len(calls) == 2:
            # Iron condor: short outer puts/calls, long inner
            # outer short put + inner long put + inner long call + outer short call
            if (puts[0].quantity < 0 and puts[1].quantity > 0 and
                    calls[0].quantity > 0 and calls[1].quantity < 0):
                ks = f"{int(_strike(puts[0]))}/{int(_strike(puts[1]))}/{int(_strike(calls[0]))}/{int(_strike(calls[1]))}"
                return f"Iron Condor {ks}"
            # Iron butterfly: two short legs share ATM strike (one put, one call)
            short_legs = [p for p in positions if p.quantity < 0]
            long_legs  = [p for p in positions if p.quantity > 0]
            if len(short_legs) == 2 and len(long_legs) == 2:
                short_strikes = {_strike(p) for p in short_legs}
                if len(short_strikes) == 1:
                    atm = int(next(iter(short_strikes)))
                    outer_puts  = [p for p in long_legs if _right(p) == "P"]
                    outer_calls = [p for p in long_legs if _right(p) == "C"]
                    if outer_puts and outer_calls:
                        low_k  = int(_strike(outer_puts[0]))
                        high_k = int(_strike(outer_calls[0]))
                        return f"Iron Butterfly {low_k}/{atm}/{high_k}"

    # --- fallback ---
    legs_desc = "/".join(
        f"{int(_strike(p))}{'C' if _right(p) == 'C' else 'P'}"
        for p in positions
    )
    return f"Custom {n}-Leg {legs_desc}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_valid_bid(contract: OptionContract) -> bool:
    return contract.bid is not None and contract.bid > 0


def _has_valid_ask(contract: OptionContract) -> bool:
    return contract.ask is not None and contract.ask > 0


def _position_valid(contract: OptionContract, quantity: int) -> bool:
    """Return True if the required price side is available for this direction."""
    if quantity > 0:
        return _has_valid_ask(contract)
    return _has_valid_bid(contract)


def _compute_metrics(bins: list[PriceBin], strategy: Strategy) -> RiskMetrics:
    """
    Compute RiskMetrics without calling validate_for_analysis().

    The optimizer generates strategies programmatically and has already
    validated bid/ask during enumeration, so re-validation is unnecessary
    and would incorrectly block stock-only strategies.
    """
    ev = calculate_expected_value(bins, strategy)
    pop = calculate_probability_of_profit(bins, strategy)
    payoffs = [strategy.total_payoff(b.midpoint) for b in bins]
    return RiskMetrics(
        expected_value=ev,
        probability_of_profit=pop,
        max_gain=max(payoffs),
        max_loss=min(payoffs),
        breakevens=[],
    )


def _is_loss_unbounded(strategy: Strategy) -> bool:
    """
    Return True when the strategy has theoretically unlimited upside loss.

    Criteria:
    - Net short calls with no long call hedge above (naked short call exposure)
    - Short stock position with no net long call position to cap upside

    Short puts are NOT considered unbounded (max loss is finite: strike × multiplier).
    Long options are never unbounded.
    """
    net_call_qty = sum(
        p.quantity for p in strategy.option_positions if p.contract.right == "C"
    )
    # Naked short call exposure
    if net_call_qty < 0:
        return True
    # Short stock without call hedge
    if strategy.stock_quantity < 0 and net_call_qty <= 0:
        return True
    return False


def _percentile_loss(bins: list[PriceBin], strategy: Strategy, pct: float = 0.05) -> float:
    """
    Return the payoff at the given lower percentile of the price distribution.

    Used as the max-loss estimate for strategies where abs(max_loss) < 1e-2
    (e.g. strategies that never lose within the distribution range).
    """
    total = sum(b.count for b in bins)
    target = total * pct
    cumulative = 0.0
    worst_payoff = min(strategy.total_payoff(b.midpoint) for b in bins)
    for b in sorted(bins, key=lambda b: b.midpoint):
        cumulative += b.count
        if cumulative >= target:
            return strategy.total_payoff(b.midpoint)
    return worst_payoff


def theoretical_max_loss(strategy: Strategy) -> float:
    """
    Evaluate payoff at boundary prices to find the worst-case theoretical loss.

    Checks payoff at:
      - price = 0.01 (near-zero; worst case for long puts / short stock)
      - Just below, at, and just above each option strike (payoff kink points)
      - 3× current price (worst case for short calls / long stock)

    Used to detect strategies whose loss zone falls entirely outside the MC
    price distribution, which would otherwise make them appear risk-free.
    """
    current = strategy.stock.current_price
    strikes = [p.contract.strike for p in strategy.option_positions]

    test_prices: list[float] = [0.01]
    for k in strikes:
        test_prices.extend([max(0.01, k - 0.01), k, k + 0.01])
    test_prices.append(current * 3.0)

    return min(strategy.total_payoff(p) for p in test_prices)


def _ev_to_risk(metrics: RiskMetrics, bins: list[PriceBin], strategy: Strategy) -> tuple[float, bool]:
    """
    Compute (ev_to_risk ratio, loss_unbounded flag).

    Uses the more conservative of the MC-derived max_loss and the theoretical
    max_loss evaluated at boundary prices. This prevents strategies whose loss
    zone falls outside the MC price range from appearing risk-free and receiving
    an inflated ratio.
    """
    unbounded = _is_loss_unbounded(strategy)
    mc_max_loss = metrics.max_loss

    # Theoretical worst-case payoff at boundary prices (strikes, price=0, 3× price)
    theoretical = theoretical_max_loss(strategy)

    # Use the more conservative (more negative) of the two
    max_loss = min(mc_max_loss, theoretical) if mc_max_loss is not None else theoretical

    if max_loss >= -1e-2:
        # No meaningful loss found — use 5th percentile as last resort
        estimated = _percentile_loss(bins, strategy, pct=0.05)
        denom = abs(estimated) if abs(estimated) >= 1e-2 else 1.0
        return metrics.expected_value / denom, True

    return metrics.expected_value / abs(max_loss), unbounded


# ---------------------------------------------------------------------------
# Enumerators
# ---------------------------------------------------------------------------


def _enum_1leg(chain: OptionChain, stock: Stock):
    """Yield all valid single-leg option strategies (long and short each contract)."""
    all_contracts = chain.calls + chain.puts
    for contract in all_contracts:
        for qty in (1, -1):
            if _position_valid(contract, qty):
                yield Strategy(
                    stock=stock,
                    stock_quantity=0,
                    option_positions=[OptionPosition(contract=contract, quantity=qty)],
                )


def _enum_2leg(chain: OptionChain, stock: Stock):
    """Yield all valid 2-leg strategies: every pair of contracts in both directions."""
    all_contracts = chain.calls + chain.puts
    for a, b in combinations(all_contracts, 2):
        # Direction 1: long A + short B
        if _position_valid(a, 1) and _position_valid(b, -1):
            yield Strategy(
                stock=stock,
                stock_quantity=0,
                option_positions=[
                    OptionPosition(contract=a, quantity=1),
                    OptionPosition(contract=b, quantity=-1),
                ],
            )
        # Direction 2: short A + long B
        if _position_valid(a, -1) and _position_valid(b, 1):
            yield Strategy(
                stock=stock,
                stock_quantity=0,
                option_positions=[
                    OptionPosition(contract=a, quantity=-1),
                    OptionPosition(contract=b, quantity=1),
                ],
            )


def _enum_named_multileg(chain: OptionChain, stock: Stock):
    """Yield iron condors, call/put butterflies, and iron butterflies."""
    calls = sorted(chain.calls, key=lambda c: c.strike)
    puts  = sorted(chain.puts,  key=lambda c: c.strike)

    # --- Iron condor ---
    # short put K1, long put K2, long call K3, short call K4  (K1 < K2 < K3 < K4)
    for (p1, p2) in combinations(puts, 2):
        # p1.strike < p2.strike (sorted)
        for (c1, c2) in combinations(calls, 2):
            # c1.strike < c2.strike (sorted)
            if p2.strike >= c1.strike:
                continue  # need gap between put wing and call wing
            if not all([
                _position_valid(p1, -1),
                _position_valid(p2, 1),
                _position_valid(c1, 1),
                _position_valid(c2, -1),
            ]):
                continue
            yield Strategy(
                stock=stock,
                stock_quantity=0,
                option_positions=[
                    OptionPosition(contract=p1, quantity=-1),
                    OptionPosition(contract=p2, quantity=1),
                    OptionPosition(contract=c1, quantity=1),
                    OptionPosition(contract=c2, quantity=-1),
                ],
            )

    # --- Call butterfly ---
    # long c1, short 2× c2, long c3  (c1.strike < c2.strike < c3.strike)
    for (c1, c2, c3) in combinations(calls, 3):
        if not all([
            _position_valid(c1, 1),
            _position_valid(c2, -1),
            _position_valid(c3, 1),
        ]):
            continue
        yield Strategy(
            stock=stock,
            stock_quantity=0,
            option_positions=[
                OptionPosition(contract=c1, quantity=1),
                OptionPosition(contract=c2, quantity=-2),
                OptionPosition(contract=c3, quantity=1),
            ],
        )

    # --- Put butterfly ---
    # long p1, short 2× p2, long p3
    for (p1, p2, p3) in combinations(puts, 3):
        if not all([
            _position_valid(p1, 1),
            _position_valid(p2, -1),
            _position_valid(p3, 1),
        ]):
            continue
        yield Strategy(
            stock=stock,
            stock_quantity=0,
            option_positions=[
                OptionPosition(contract=p1, quantity=1),
                OptionPosition(contract=p2, quantity=-2),
                OptionPosition(contract=p3, quantity=1),
            ],
        )

    # --- Iron butterfly ---
    # long p_lower, short p_atm, short c_atm, long c_upper
    # ATM strike must appear in both calls and puts
    call_strikes = {c.strike: c for c in calls}
    put_strikes  = {p.strike: p for p in puts}
    atm_strikes  = set(call_strikes) & set(put_strikes)

    for atm_k in sorted(atm_strikes):
        atm_call = call_strikes[atm_k]
        atm_put  = put_strikes[atm_k]
        lower_puts  = [p for p in puts  if p.strike < atm_k]
        upper_calls = [c for c in calls if c.strike > atm_k]
        for p_lower in lower_puts:
            for c_upper in upper_calls:
                if not all([
                    _position_valid(p_lower, 1),
                    _position_valid(atm_put, -1),
                    _position_valid(atm_call, -1),
                    _position_valid(c_upper, 1),
                ]):
                    continue
                yield Strategy(
                    stock=stock,
                    stock_quantity=0,
                    option_positions=[
                        OptionPosition(contract=p_lower,  quantity=1),
                        OptionPosition(contract=atm_put,  quantity=-1),
                        OptionPosition(contract=atm_call, quantity=-1),
                        OptionPosition(contract=c_upper,  quantity=1),
                    ],
                )


def _enum_stock_with_options(chain: OptionChain, stock: Stock, stock_qty: int):
    """
    Yield strategies with ±stock_qty shares combined with 0, 1, or 2 option legs.

    Each combination of stock direction (long/short) and option legs is explored.
    """
    all_contracts = chain.calls + chain.puts

    for sq in (stock_qty, -stock_qty):
        # 0 legs: pure stock
        yield Strategy(stock=stock, stock_quantity=sq, option_positions=[])

        # 1 leg
        for contract in all_contracts:
            for qty in (1, -1):
                if _position_valid(contract, qty):
                    yield Strategy(
                        stock=stock,
                        stock_quantity=sq,
                        option_positions=[OptionPosition(contract=contract, quantity=qty)],
                    )

        # 2 legs
        for a, b in combinations(all_contracts, 2):
            if _position_valid(a, 1) and _position_valid(b, -1):
                yield Strategy(
                    stock=stock,
                    stock_quantity=sq,
                    option_positions=[
                        OptionPosition(contract=a, quantity=1),
                        OptionPosition(contract=b, quantity=-1),
                    ],
                )
            if _position_valid(a, -1) and _position_valid(b, 1):
                yield Strategy(
                    stock=stock,
                    stock_quantity=sq,
                    option_positions=[
                        OptionPosition(contract=a, quantity=-1),
                        OptionPosition(contract=b, quantity=1),
                    ],
                )


# ---------------------------------------------------------------------------
# Candidate counting (for progress reporting)
# ---------------------------------------------------------------------------


def _count_candidates(
    chain: OptionChain,
    include_1leg: bool,
    include_2leg: bool,
    include_named_multileg: bool,
    include_stock_legs: bool,
) -> int:
    """
    Upper-bound estimate of candidates before bid/ask filtering.

    Used to provide a denominator for progress reporting. Actual processed
    count will be slightly lower because invalid bid/ask contracts are skipped.
    """
    nc = len(chain.calls)
    np_ = len(chain.puts)
    n = nc + np_
    total = 0
    if include_1leg:
        total += 2 * n
    if include_2leg:
        total += comb(n, 2) * 2
    if include_named_multileg:
        total += comb(nc, 2) * comb(np_, 2)            # iron condors
        total += comb(nc, 3) + comb(np_, 3)            # call + put butterflies
        atm = len(
            {c.strike for c in chain.calls} & {p.strike for p in chain.puts}
        )
        total += atm * max(nc - 1, 0) * max(np_ - 1, 0)  # iron butterflies
    if include_stock_legs:
        n_combos = 1 + 2 * n + comb(n, 2) * 2         # 0 / 1 / 2 option legs
        total += 2 * n_combos                           # long + short stock
    return max(total, 1)


# ---------------------------------------------------------------------------
# Constraint check
# ---------------------------------------------------------------------------


def _passes_filters(
    metrics: RiskMetrics,
    max_loss_limit: float | None,
    min_pop: float | None,
    min_ev: float | None,
) -> bool:
    if max_loss_limit is not None and metrics.max_loss is not None:
        if metrics.max_loss < -max_loss_limit:
            return False
    if min_pop is not None and metrics.probability_of_profit < min_pop:
        return False
    if min_ev is not None and metrics.expected_value < min_ev:
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def optimize_strategies(
    bins: list[PriceBin],
    chain: OptionChain,
    stock: Stock,
    *,
    include_1leg: bool = True,
    include_2leg: bool = True,
    include_named_multileg: bool = True,
    include_stock_legs: bool = True,
    stock_qty: int = 100,
    max_loss_limit: float | None = None,
    min_pop: float | None = None,
    min_ev: float | None = None,
    top_n: int = 20,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[StrategyResult]:
    """
    Enumerate and rank option strategies by EV-to-risk ratio.

    Args:
        bins: Pre-computed Monte Carlo price distribution histogram.
        chain: Option chain with calls and puts for a single expiration.
        stock: Underlying stock.
        include_1leg: Include single-leg positions.
        include_2leg: Include all exhaustive 2-leg combinations.
        include_named_multileg: Include iron condors, butterflies, iron butterflies.
        include_stock_legs: Include stock ± 0–2 option leg strategies.
        stock_qty: Absolute share count for stock-leg strategies.
        max_loss_limit: Exclude strategies whose max_loss < -limit.
        min_pop: Exclude strategies with probability_of_profit < min_pop.
        min_ev: Exclude strategies with expected_value < min_ev.
        top_n: Maximum number of results (sorted descending by ev_to_risk).
        on_progress: Optional callback(processed, total) called roughly every 1%
            of candidates. Used for progress reporting in streaming endpoints.

    Returns:
        Up to top_n StrategyResult objects sorted by ev_to_risk descending.

    Raises:
        ValueError: If bins is empty.
    """
    if not bins:
        raise ValueError("bins cannot be empty")

    # Collect candidate generators
    generators = []
    if include_1leg:
        generators.append(_enum_1leg(chain, stock))
    if include_2leg:
        generators.append(_enum_2leg(chain, stock))
    if include_named_multileg:
        generators.append(_enum_named_multileg(chain, stock))
    if include_stock_legs:
        generators.append(_enum_stock_with_options(chain, stock, stock_qty))

    # Upper-bound candidate count for progress denominator
    total = _count_candidates(chain, include_1leg, include_2leg, include_named_multileg, include_stock_legs)
    interval = max(1, total // 100)  # ~100 progress ticks

    results: list[StrategyResult] = []
    processed = 0

    for gen in generators:
        for strategy in gen:
            processed += 1
            if on_progress is not None and processed % interval == 0:
                on_progress(processed, total)

            try:
                metrics = _compute_metrics(bins, strategy)
            except Exception:
                continue

            if not _passes_filters(metrics, max_loss_limit, min_pop, min_ev):
                continue

            ratio, unbounded = _ev_to_risk(metrics, bins, strategy)
            results.append(StrategyResult(
                strategy=strategy,
                metrics=metrics,
                ev_to_risk=ratio,
                loss_unbounded=unbounded,
                description=describe_strategy(strategy),
            ))

    if on_progress is not None:
        on_progress(processed, total)

    results.sort(key=lambda r: r.ev_to_risk, reverse=True)
    return results[:top_n]
