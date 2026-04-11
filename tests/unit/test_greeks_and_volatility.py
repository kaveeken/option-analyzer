"""
Unit tests for greeks, implied volatility, and dividend data.

Tests cover:
- Domain model fields for Stock (vol/dividend) and OptionContract (greeks/IV)
- IBKR client _parse_market_snapshot with new field codes
- IBKR client get_stock populates vol/dividend fields
- IBKR client price_option_chain populates greeks/IV on contracts
- API responses include new fields via GET /api/stocks and GET /api/stocks/chains
- Position data stores and returns greeks
"""

from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from option_analyzer.api.app import create_app
from option_analyzer.api.dependencies import (
    get_ibkr_client,
    get_plot_executor_dep,
    get_session_service_dep,
)
from option_analyzer.clients.cache import InMemoryCache
from option_analyzer.clients.ibkr import IBKRClient, _parse_float_field
from option_analyzer.config import Settings
from option_analyzer.models.domain import OptionChain, OptionContract, Stock
from option_analyzer.services.session import SessionService
from option_analyzer.utils.rate_limiter import RateLimiter

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def rate_limiter() -> RateLimiter:
    return RateLimiter(max_requests=50, per_seconds=1.0)


@pytest.fixture
async def client(settings, cache, rate_limiter) -> IBKRClient:
    async with IBKRClient(settings, cache, rate_limiter) as client:
        yield client


@pytest.fixture
def mock_ibkr_client():
    return Mock(spec=IBKRClient)


@pytest.fixture
def session_service():
    return SessionService(ttl_seconds=3600)


@pytest.fixture
def test_client(mock_ibkr_client, session_service):
    from concurrent.futures import ThreadPoolExecutor
    app = create_app()
    executor = ThreadPoolExecutor(max_workers=1)
    app.dependency_overrides[get_ibkr_client] = lambda: mock_ibkr_client
    app.dependency_overrides[get_session_service_dep] = lambda: session_service
    app.dependency_overrides[get_plot_executor_dep] = lambda: executor
    yield TestClient(app)
    executor.shutdown(wait=False)


# ============================================================================
# _parse_float_field helper
# ============================================================================


class TestParseFloatField:
    """Test the _parse_float_field module-level helper."""

    def test_none_returns_none(self) -> None:
        assert _parse_float_field(None) is None

    def test_float_value(self) -> None:
        assert _parse_float_field(0.45) == pytest.approx(0.45)

    def test_int_value(self) -> None:
        assert _parse_float_field(1) == pytest.approx(1.0)

    def test_string_float(self) -> None:
        assert _parse_float_field("0.45") == pytest.approx(0.45)

    def test_string_with_c_prefix(self) -> None:
        """IBKR prefixes close price with 'C'."""
        assert _parse_float_field("C150.25") == pytest.approx(150.25)

    def test_string_with_h_prefix(self) -> None:
        """IBKR prefixes halted price with 'H'."""
        assert _parse_float_field("H150.25") == pytest.approx(150.25)

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_float_field("not_a_number") is None

    def test_negative_value(self) -> None:
        """Theta is typically negative."""
        assert _parse_float_field("-0.05") == pytest.approx(-0.05)
        assert _parse_float_field(-0.05) == pytest.approx(-0.05)

    def test_string_with_percent_suffix(self) -> None:
        """IBKR returns volatility fields (7283, 7087, 7084) with a '%' suffix."""
        assert _parse_float_field("24.239%") == pytest.approx(24.239)
        assert _parse_float_field("101.4%") == pytest.approx(101.4)


# ============================================================================
# Domain Model: Stock vol/dividend fields
# ============================================================================


class TestStockVolatilityFields:
    """Test that Stock model supports vol/dividend fields."""

    def test_stock_defaults_vol_fields_to_none(self) -> None:
        stock = Stock(symbol="AAPL", current_price=150.0, conid=265598)
        assert stock.iv_30d is None
        assert stock.hist_vol is None
        assert stock.iv_hv_ratio is None
        assert stock.dividends_forward is None
        assert stock.dividends_ttm is None

    def test_stock_accepts_vol_fields(self) -> None:
        stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            iv_30d=28.5,
            hist_vol=22.3,
            iv_hv_ratio=127.9,
            dividends_forward=0.96,
            dividends_ttm=0.92,
        )
        assert stock.iv_30d == pytest.approx(28.5)
        assert stock.hist_vol == pytest.approx(22.3)
        assert stock.iv_hv_ratio == pytest.approx(127.9)
        assert stock.dividends_forward == pytest.approx(0.96)
        assert stock.dividends_ttm == pytest.approx(0.92)

    def test_stock_partial_vol_fields(self) -> None:
        """Partial data is fine — IBKR may not return all fields."""
        stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            iv_30d=28.5,
        )
        assert stock.iv_30d == pytest.approx(28.5)
        assert stock.hist_vol is None
        assert stock.dividends_forward is None


# ============================================================================
# Domain Model: OptionContract greek fields
# ============================================================================


class TestOptionContractGreekFields:
    """Test that OptionContract model supports greek/IV fields."""

    def test_contract_defaults_greeks_to_none(self) -> None:
        contract = OptionContract(
            conid=100001,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
        )
        assert contract.delta is None
        assert contract.gamma is None
        assert contract.theta is None
        assert contract.vega is None
        assert contract.implied_volatility is None

    def test_contract_accepts_greeks(self) -> None:
        contract = OptionContract(
            conid=100001,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            delta=0.45,
            gamma=0.02,
            theta=-0.05,
            vega=0.15,
            implied_volatility=28.5,
        )
        assert contract.delta == pytest.approx(0.45)
        assert contract.gamma == pytest.approx(0.02)
        assert contract.theta == pytest.approx(-0.05)
        assert contract.vega == pytest.approx(0.15)
        assert contract.implied_volatility == pytest.approx(28.5)

    def test_put_accepts_negative_delta(self) -> None:
        """Put delta is negative."""
        contract = OptionContract(
            conid=200001,
            strike=150.0,
            right="P",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            delta=-0.45,
        )
        assert contract.delta == pytest.approx(-0.45)


# ============================================================================
# IBKR _parse_market_snapshot
# ============================================================================


class TestParseMarketSnapshot:
    """Test _parse_market_snapshot extracts all new field codes."""

    @pytest.mark.asyncio
    async def test_parses_greeks_when_present(self, client: IBKRClient) -> None:
        raw = {
            "conid": 100001,
            "31": "150.25",
            "84": "150.20",
            "86": "150.30",
            "7308": "0.45",   # delta
            "7309": "0.02",   # gamma
            "7310": "-0.05",  # theta
            "7311": "0.15",   # vega
            "7633": "28.5",   # implied vol per-strike
        }
        result = client._parse_market_snapshot(raw)
        assert result["delta"] == pytest.approx(0.45)
        assert result["gamma"] == pytest.approx(0.02)
        assert result["theta"] == pytest.approx(-0.05)
        assert result["vega"] == pytest.approx(0.15)
        assert result["implied_volatility"] == pytest.approx(28.5)

    @pytest.mark.asyncio
    async def test_greeks_none_when_absent(self, client: IBKRClient) -> None:
        raw = {
            "conid": 100001,
            "31": "150.25",
            "84": "150.20",
            "86": "150.30",
            # No greek fields
        }
        result = client._parse_market_snapshot(raw)
        assert result["delta"] is None
        assert result["gamma"] is None
        assert result["theta"] is None
        assert result["vega"] is None
        assert result["implied_volatility"] is None

    @pytest.mark.asyncio
    async def test_parses_stock_vol_fields(self, client: IBKRClient) -> None:
        raw = {
            "conid": 265598,
            "31": "150.25",
            "84": "150.20",
            "86": "150.30",
            "7283": "28.5",   # IV 30d on underlying
            "7087": "22.3",   # hist vol
            "7084": "127.9",  # IV/HV ratio
        }
        result = client._parse_market_snapshot(raw)
        assert result["iv_30d"] == pytest.approx(28.5)
        assert result["hist_vol"] == pytest.approx(22.3)
        assert result["iv_hv_ratio"] == pytest.approx(127.9)

    @pytest.mark.asyncio
    async def test_parses_dividend_fields(self, client: IBKRClient) -> None:
        raw = {
            "conid": 265598,
            "31": "150.25",
            "84": "150.20",
            "86": "150.30",
            "7671": "0.96",   # dividends forward 12mo
            "7672": "0.92",   # dividends TTM
        }
        result = client._parse_market_snapshot(raw)
        assert result["dividends_forward"] == pytest.approx(0.96)
        assert result["dividends_ttm"] == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_vol_dividend_fields_none_when_absent(self, client: IBKRClient) -> None:
        raw = {
            "conid": 265598,
            "31": "150.25",
            "84": "150.20",
            "86": "150.30",
        }
        result = client._parse_market_snapshot(raw)
        assert result["iv_30d"] is None
        assert result["hist_vol"] is None
        assert result["iv_hv_ratio"] is None
        assert result["dividends_forward"] is None
        assert result["dividends_ttm"] is None

    @pytest.mark.asyncio
    async def test_parses_numeric_greeks(self, client: IBKRClient) -> None:
        """Handles numeric (non-string) values from the snapshot."""
        raw = {
            "conid": 100001,
            "31": 150.25,
            "84": 150.20,
            "86": 150.30,
            "7308": 0.45,
            "7309": 0.02,
        }
        result = client._parse_market_snapshot(raw)
        assert result["delta"] == pytest.approx(0.45)
        assert result["gamma"] == pytest.approx(0.02)


# ============================================================================
# IBKR get_stock vol/dividend population
# ============================================================================


class TestGetStockVolatilityFields:
    """Test that get_stock populates vol/dividend fields from snapshot."""

    @pytest.mark.asyncio
    async def test_get_stock_populates_vol_fields(self, client: IBKRClient) -> None:
        mock_search = [{
            "conid": 265598,
            "symbol": "AAPL",
            "sections": [{"secType": "OPT", "months": "JAN26;FEB26"}],
        }]
        mock_snapshot = [{
            "conid": 265598,
            "last": 150.25,
            "bid": 150.20,
            "ask": 150.30,
            "iv_30d": 28.5,
            "hist_vol": 22.3,
            "iv_hv_ratio": 127.9,
            "dividends_forward": 0.96,
            "dividends_ttm": 0.92,
            # greeks are None for stock snapshot (not applicable)
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "implied_volatility": None,
        }]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        stock = await client.get_stock("AAPL")

        assert stock.iv_30d == pytest.approx(28.5)
        assert stock.hist_vol == pytest.approx(22.3)
        assert stock.iv_hv_ratio == pytest.approx(127.9)
        assert stock.dividends_forward == pytest.approx(0.96)
        assert stock.dividends_ttm == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_get_stock_vol_fields_none_when_absent(self, client: IBKRClient) -> None:
        """Vol fields are None when IBKR snapshot doesn't include them."""
        mock_search = [{
            "conid": 265598,
            "symbol": "AAPL",
            "sections": [{"secType": "OPT", "months": "JAN26"}],
        }]
        mock_snapshot = [{
            "conid": 265598,
            "last": 150.25,
            "bid": 150.20,
            "ask": 150.30,
            # No vol/dividend fields
        }]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        stock = await client.get_stock("AAPL")

        assert stock.iv_30d is None
        assert stock.hist_vol is None
        assert stock.dividends_forward is None

    @pytest.mark.asyncio
    async def test_get_stock_snapshot_requests_stock_fields(self, client: IBKRClient) -> None:
        """get_stock requests vol and dividend field codes from IBKR."""
        mock_search = [{
            "conid": 265598,
            "symbol": "AAPL",
            "sections": [{"secType": "OPT", "months": "JAN26"}],
        }]
        mock_snapshot = [{"conid": 265598, "last": 150.0, "bid": 149.95, "ask": 150.05}]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        await client.get_stock("AAPL")

        call_kwargs = client.get_market_snapshot.call_args
        # Verify fields string contains vol and dividend field codes
        fields_arg = call_kwargs[1].get("fields") or (call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None)
        assert fields_arg is not None
        for code in ["7283", "7087", "7084", "7671", "7672"]:
            assert code in fields_arg, f"Expected field {code} in fields: {fields_arg}"


# ============================================================================
# IBKR price_option_chain greeks population
# ============================================================================


class TestPriceOptionChainGreeks:
    """Test that price_option_chain populates greeks/IV on contracts."""

    def _make_chain(self) -> OptionChain:
        expiration = date(2026, 1, 16)
        return OptionChain(
            expiration=expiration,
            calls=[
                OptionContract(conid=100001, strike=150.0, right="C", expiration=expiration),
            ],
            puts=[
                OptionContract(conid=200001, strike=150.0, right="P", expiration=expiration),
            ],
        )

    @pytest.mark.asyncio
    async def test_price_option_chain_populates_greeks(self, client: IBKRClient) -> None:
        chain = self._make_chain()

        mock_snapshot = [
            {
                "conid": 100001,
                "last": 2.50,
                "bid": 2.45,
                "ask": 2.55,
                "delta": 0.45,
                "gamma": 0.02,
                "theta": -0.05,
                "vega": 0.15,
                "implied_volatility": 28.5,
                "iv_30d": None,
                "hist_vol": None,
                "iv_hv_ratio": None,
                "dividends_forward": None,
                "dividends_ttm": None,
            },
            {
                "conid": 200001,
                "last": 2.50,
                "bid": 2.45,
                "ask": 2.55,
                "delta": -0.45,
                "gamma": 0.02,
                "theta": -0.05,
                "vega": 0.15,
                "implied_volatility": 28.5,
                "iv_30d": None,
                "hist_vol": None,
                "iv_hv_ratio": None,
                "dividends_forward": None,
                "dividends_ttm": None,
            },
        ]
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        await client.price_option_chain(chain)

        call = chain.calls[0]
        assert call.bid == pytest.approx(2.45)
        assert call.ask == pytest.approx(2.55)
        assert call.delta == pytest.approx(0.45)
        assert call.gamma == pytest.approx(0.02)
        assert call.theta == pytest.approx(-0.05)
        assert call.vega == pytest.approx(0.15)
        assert call.implied_volatility == pytest.approx(28.5)

        put = chain.puts[0]
        assert put.delta == pytest.approx(-0.45)
        assert put.implied_volatility == pytest.approx(28.5)

    @pytest.mark.asyncio
    async def test_price_option_chain_greeks_none_when_absent(self, client: IBKRClient) -> None:
        """Greeks remain None if snapshot doesn't include them."""
        chain = self._make_chain()
        mock_snapshot = [
            {
                "conid": 100001,
                "last": 2.50,
                "bid": 2.45,
                "ask": 2.55,
                "delta": None,
                "gamma": None,
                "theta": None,
                "vega": None,
                "implied_volatility": None,
                "iv_30d": None,
                "hist_vol": None,
                "iv_hv_ratio": None,
                "dividends_forward": None,
                "dividends_ttm": None,
            },
            {
                "conid": 200001,
                "last": 2.50,
                "bid": 2.45,
                "ask": 2.55,
                "delta": None,
                "gamma": None,
                "theta": None,
                "vega": None,
                "implied_volatility": None,
                "iv_30d": None,
                "hist_vol": None,
                "iv_hv_ratio": None,
                "dividends_forward": None,
                "dividends_ttm": None,
            },
        ]
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        await client.price_option_chain(chain)

        call = chain.calls[0]
        assert call.delta is None
        assert call.gamma is None
        assert call.theta is None
        assert call.vega is None
        assert call.implied_volatility is None

    @pytest.mark.asyncio
    async def test_price_option_chain_requests_greek_fields(self, client: IBKRClient) -> None:
        """price_option_chain requests greek field codes from IBKR."""
        chain = self._make_chain()
        mock_snapshot = [
            {
                "conid": 100001, "last": 2.50, "bid": 2.45, "ask": 2.55,
                "delta": 0.45, "gamma": 0.02, "theta": -0.05, "vega": 0.15,
                "implied_volatility": 28.5, "iv_30d": None, "hist_vol": None,
                "iv_hv_ratio": None, "dividends_forward": None, "dividends_ttm": None,
            },
            {
                "conid": 200001, "last": 2.50, "bid": 2.45, "ask": 2.55,
                "delta": -0.45, "gamma": 0.02, "theta": -0.05, "vega": 0.15,
                "implied_volatility": 28.5, "iv_30d": None, "hist_vol": None,
                "iv_hv_ratio": None, "dividends_forward": None, "dividends_ttm": None,
            },
        ]
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        await client.price_option_chain(chain)

        call_kwargs = client.get_market_snapshot.call_args
        fields_arg = call_kwargs[1].get("fields") or (call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None)
        assert fields_arg is not None
        for code in ["7308", "7309", "7310", "7311", "7633"]:
            assert code in fields_arg, f"Expected field {code} in fields: {fields_arg}"


# ============================================================================
# API: GET /api/stocks/{symbol} includes vol/dividend fields
# ============================================================================


class TestStocksEndpointVolatility:
    """Test that GET /api/stocks/{symbol} includes vol/dividend fields."""

    def test_stock_response_includes_vol_fields(self, test_client, mock_ibkr_client) -> None:
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
            iv_30d=28.5,
            hist_vol=22.3,
            iv_hv_ratio=127.9,
            dividends_forward=0.96,
            dividends_ttm=0.92,
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        response = test_client.get("/api/stocks/AAPL")

        assert response.status_code == 200
        data = response.json()
        assert data["iv_30d"] == pytest.approx(28.5)
        assert data["hist_vol"] == pytest.approx(22.3)
        assert data["iv_hv_ratio"] == pytest.approx(127.9)
        assert data["dividends_forward"] == pytest.approx(0.96)
        assert data["dividends_ttm"] == pytest.approx(0.92)

    def test_stock_response_vol_fields_null_when_absent(
        self, test_client, mock_ibkr_client
    ) -> None:
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
            # No vol/dividend fields
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        response = test_client.get("/api/stocks/AAPL")

        assert response.status_code == 200
        data = response.json()
        assert data["iv_30d"] is None
        assert data["hist_vol"] is None
        assert data["iv_hv_ratio"] is None
        assert data["dividends_forward"] is None
        assert data["dividends_ttm"] is None


# ============================================================================
# API: GET /api/stocks/{symbol}/chains includes greeks
# ============================================================================


class TestOptionChainEndpointGreeks:
    """Test that GET /api/stocks/{symbol}/chains includes greek fields."""

    def test_option_chain_response_includes_greeks(
        self, test_client, mock_ibkr_client
    ) -> None:
        expiration = date(2026, 1, 16)
        mock_stock = Stock(
            symbol="AAPL", current_price=150.25, conid=265598,
            available_expirations=["JAN26"],
        )
        mock_chain = OptionChain(
            expiration=expiration,
            calls=[
                OptionContract(
                    conid=100001, strike=150.0, right="C", expiration=expiration,
                    bid=2.45, ask=2.55,
                    delta=0.45, gamma=0.02, theta=-0.05, vega=0.15,
                    implied_volatility=28.5,
                )
            ],
            puts=[
                OptionContract(
                    conid=200001, strike=150.0, right="P", expiration=expiration,
                    bid=2.45, ask=2.55,
                    delta=-0.45, gamma=0.02, theta=-0.05, vega=0.15,
                    implied_volatility=28.5,
                )
            ],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        response = test_client.get("/api/stocks/AAPL/chains?month=JAN26")

        assert response.status_code == 200
        data = response.json()
        call = data["calls"][0]
        assert call["delta"] == pytest.approx(0.45)
        assert call["gamma"] == pytest.approx(0.02)
        assert call["theta"] == pytest.approx(-0.05)
        assert call["vega"] == pytest.approx(0.15)
        assert call["implied_volatility"] == pytest.approx(28.5)

        put = data["puts"][0]
        assert put["delta"] == pytest.approx(-0.45)

    def test_option_chain_response_greeks_null_when_absent(
        self, test_client, mock_ibkr_client
    ) -> None:
        expiration = date(2026, 1, 16)
        mock_stock = Stock(
            symbol="AAPL", current_price=150.25, conid=265598,
            available_expirations=["JAN26"],
        )
        mock_chain = OptionChain(
            expiration=expiration,
            calls=[
                OptionContract(
                    conid=100001, strike=150.0, right="C", expiration=expiration,
                    bid=2.45, ask=2.55,
                    # No greeks
                )
            ],
            puts=[],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        response = test_client.get("/api/stocks/AAPL/chains?month=JAN26")

        assert response.status_code == 200
        data = response.json()
        call = data["calls"][0]
        assert call["delta"] is None
        assert call["gamma"] is None
        assert call["theta"] is None
        assert call["vega"] is None
        assert call["implied_volatility"] is None


# ============================================================================
# API: Positions store and return greeks
# ============================================================================


class TestPositionGreeks:
    """Test that positions store and return greek fields."""

    def _init_session(self, test_client, mock_ibkr_client) -> str:
        """Initialize a strategy session and return session cookie."""
        mock_stock = Stock(
            symbol="AAPL", current_price=150.25, conid=265598,
            available_expirations=["JAN26", "FEB26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert response.status_code == 200
        return response.cookies.get("session_id")

    def test_add_position_response_includes_greeks(
        self, test_client, mock_ibkr_client
    ) -> None:
        """Adding a position with greeks returns them in PositionsResponse."""
        self._init_session(test_client, mock_ibkr_client)

        expiration = date(2026, 1, 16)
        mock_stock = Stock(
            symbol="AAPL", current_price=150.25, conid=265598,
            available_expirations=["JAN26"],
        )
        call_contract = OptionContract(
            conid=100001, strike=150.0, right="C", expiration=expiration,
            bid=2.45, ask=2.55,
            delta=0.45, gamma=0.02, theta=-0.05, vega=0.15,
            implied_volatility=28.5,
        )
        mock_chain = OptionChain(
            expiration=expiration, calls=[call_contract], puts=[],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 100001, "quantity": 1},
        )

        assert response.status_code == 200
        positions = response.json()["positions"]
        assert len(positions) == 1
        pos = positions[0]
        assert pos["delta"] == pytest.approx(0.45)
        assert pos["gamma"] == pytest.approx(0.02)
        assert pos["theta"] == pytest.approx(-0.05)
        assert pos["vega"] == pytest.approx(0.15)
        assert pos["implied_volatility"] == pytest.approx(28.5)

    def test_add_position_greeks_null_when_not_available(
        self, test_client, mock_ibkr_client
    ) -> None:
        """Position greeks are null when IBKR didn't return them."""
        self._init_session(test_client, mock_ibkr_client)

        expiration = date(2026, 1, 16)
        mock_stock = Stock(
            symbol="AAPL", current_price=150.25, conid=265598,
            available_expirations=["JAN26"],
        )
        call_contract = OptionContract(
            conid=100001, strike=150.0, right="C", expiration=expiration,
            bid=2.45, ask=2.55,
            # No greeks
        )
        mock_chain = OptionChain(expiration=expiration, calls=[call_contract], puts=[])
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 100001, "quantity": 1},
        )

        assert response.status_code == 200
        pos = response.json()["positions"][0]
        assert pos["delta"] is None
        assert pos["gamma"] is None
        assert pos["theta"] is None
        assert pos["vega"] is None
        assert pos["implied_volatility"] is None

    def test_get_strategy_summary_includes_position_greeks(
        self, test_client, mock_ibkr_client
    ) -> None:
        """GET /api/strategy returns positions with greeks preserved from session."""
        self._init_session(test_client, mock_ibkr_client)

        expiration = date(2026, 1, 16)
        mock_stock = Stock(
            symbol="AAPL", current_price=150.25, conid=265598,
            available_expirations=["JAN26"],
        )
        call_contract = OptionContract(
            conid=100001, strike=150.0, right="C", expiration=expiration,
            bid=2.45, ask=2.55,
            delta=0.45, gamma=0.02, theta=-0.05, vega=0.15,
            implied_volatility=28.5,
        )
        mock_chain = OptionChain(expiration=expiration, calls=[call_contract], puts=[])
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Add a position
        test_client.post("/api/strategy/positions", json={"conid": 100001, "quantity": 1})

        # Get strategy summary
        summary_response = test_client.get("/api/strategy")
        assert summary_response.status_code == 200
        positions = summary_response.json()["positions"]
        assert len(positions) == 1
        pos = positions[0]
        assert pos["delta"] == pytest.approx(0.45)
        assert pos["implied_volatility"] == pytest.approx(28.5)


# ============================================================================
# API: Strategy init and summary include stock vol/dividend fields
# ============================================================================


class TestStrategyVolatilityFields:
    """Test that strategy init and summary responses include vol/dividend fields."""

    def test_strategy_init_includes_vol_fields(
        self, test_client, mock_ibkr_client
    ) -> None:
        """POST /api/strategy/init includes vol/dividend fields in response."""
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
            iv_30d=28.5,
            hist_vol=22.3,
            iv_hv_ratio=127.9,
            dividends_forward=0.96,
            dividends_ttm=0.92,
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})

        assert response.status_code == 200
        data = response.json()
        assert data["iv_30d"] == pytest.approx(28.5)
        assert data["hist_vol"] == pytest.approx(22.3)
        assert data["iv_hv_ratio"] == pytest.approx(127.9)
        assert data["dividends_forward"] == pytest.approx(0.96)
        assert data["dividends_ttm"] == pytest.approx(0.92)

    def test_strategy_init_vol_fields_null_when_absent(
        self, test_client, mock_ibkr_client
    ) -> None:
        mock_stock = Stock(
            symbol="AAPL", current_price=150.25, conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})

        assert response.status_code == 200
        data = response.json()
        assert data["iv_30d"] is None
        assert data["dividends_forward"] is None

    def test_strategy_summary_includes_vol_fields(
        self, test_client, mock_ibkr_client
    ) -> None:
        """GET /api/strategy returns vol/dividend fields stored from init."""
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
            iv_30d=28.5,
            hist_vol=22.3,
            iv_hv_ratio=127.9,
            dividends_forward=0.96,
            dividends_ttm=0.92,
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        test_client.post("/api/strategy/init", json={"symbol": "AAPL"})

        response = test_client.get("/api/strategy")

        assert response.status_code == 200
        data = response.json()
        assert data["iv_30d"] == pytest.approx(28.5)
        assert data["hist_vol"] == pytest.approx(22.3)
        assert data["iv_hv_ratio"] == pytest.approx(127.9)
        assert data["dividends_forward"] == pytest.approx(0.96)
        assert data["dividends_ttm"] == pytest.approx(0.92)

    def test_update_target_date_preserves_vol_fields(
        self, test_client, mock_ibkr_client
    ) -> None:
        """PATCH /api/strategy/target-date preserves vol/dividend fields."""
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
            iv_30d=28.5,
            dividends_forward=0.96,
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        test_client.post("/api/strategy/init", json={"symbol": "AAPL"})

        response = test_client.patch(
            "/api/strategy/target-date", json={"target_date": "FEB26"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["iv_30d"] == pytest.approx(28.5)
        assert data["dividends_forward"] == pytest.approx(0.96)

    def test_reset_strategy_preserves_vol_fields(
        self, test_client, mock_ibkr_client
    ) -> None:
        """POST /api/strategy/reset preserves vol/dividend fields."""
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
            iv_30d=28.5,
            dividends_ttm=0.92,
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        test_client.post("/api/strategy/init", json={"symbol": "AAPL"})

        response = test_client.post("/api/strategy/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["iv_30d"] == pytest.approx(28.5)
        assert data["dividends_ttm"] == pytest.approx(0.92)
