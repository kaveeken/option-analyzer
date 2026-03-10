"""
Unit tests for API endpoints.

Tests cover:
- Strategy initialization endpoint (success, no expirations, session creation)
- Stock endpoint (get stock data)
- Option chain endpoint (get option chain)
- Health check endpoint (response format)
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from option_analyzer.api.app import create_app
from option_analyzer.api.dependencies import (
    get_ibkr_client,
    get_plot_executor_dep,
    get_session_service_dep,
)
from option_analyzer.clients.ibkr import IBKRClient
from option_analyzer.models.domain import OptionChain, OptionContract, Stock
from option_analyzer.services.session import SessionService


@pytest.fixture
def mock_ibkr_client():
    """Create a mock IBKR client."""
    return Mock(spec=IBKRClient)


@pytest.fixture
def session_service():
    """Create a real session service for testing."""
    return SessionService(ttl_seconds=3600)


@pytest.fixture
def plot_executor():
    """Create a real thread pool executor for plot generation in tests."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-plot")
    yield executor
    executor.shutdown(wait=True)


@pytest.fixture
def test_client(mock_ibkr_client, session_service, plot_executor):
    """Create a test client with mocked dependencies."""
    app = create_app()

    # Override dependencies
    app.dependency_overrides[get_ibkr_client] = lambda: mock_ibkr_client
    app.dependency_overrides[get_session_service_dep] = lambda: session_service
    app.dependency_overrides[get_plot_executor_dep] = lambda: plot_executor

    return TestClient(app)


class TestStrategyInitEndpoint:
    """Test /api/strategy/init endpoint."""

    def test_strategy_init_success(self, test_client, mock_ibkr_client):
        """Test successful strategy initialization."""
        # Mock IBKR response
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26", "FEB26", "MAR26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        # Make request
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert data["current_price"] == 150.25
        assert data["target_date"] == "JAN26"  # Earliest expiration
        assert data["available_expirations"] == ["JAN26", "FEB26", "MAR26"]
        assert "session_id" in data
        assert len(data["session_id"]) > 0

        # Verify session cookie was set
        assert "session_id" in response.cookies

    def test_strategy_init_no_expirations(self, test_client, mock_ibkr_client):
        """Test strategy init raises ValidationError when no expirations available."""
        # Mock IBKR response with no expirations
        mock_stock = Stock(
            symbol="TEST",
            current_price=10.0,
            conid=12345,
            available_expirations=[],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        # Make request
        response = test_client.post("/api/strategy/init", json={"symbol": "TEST"})

        # Verify error response
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "NO_EXPIRATIONS_AVAILABLE" in data["code"]

    def test_strategy_init_creates_session(self, test_client, mock_ibkr_client, session_service):
        """Test that strategy init creates a session in the service."""
        # Mock IBKR response
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        # Verify no sessions initially
        assert session_service.session_count() == 0

        # Make request
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})

        # Verify session was created
        assert response.status_code == 200
        assert session_service.session_count() == 1

        # Verify session contains strategy data
        session_id = response.json()["session_id"]
        session = session_service.get_session(session_id)
        assert "strategy" in session.data
        assert session.data["strategy"]["symbol"] == "AAPL"
        assert session.data["strategy"]["target_date"] == "JAN26"
        assert session.data["strategy"]["positions"] == []


class TestGetStockEndpoint:
    """Test /api/stocks/{symbol} endpoint."""

    def test_get_stock(self, test_client, mock_ibkr_client):
        """Test get stock endpoint returns StockResponse format."""
        # Mock IBKR response
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        # Make request
        response = test_client.get("/api/stocks/AAPL")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert data["current_price"] == 150.25
        assert data["conid"] == 265598
        assert data["available_expirations"] == ["JAN26", "FEB26"]

        # Verify IBKR client was called with uppercase symbol
        mock_ibkr_client.get_stock.assert_called_once_with("AAPL")

    def test_get_stock_lowercase_symbol(self, test_client, mock_ibkr_client):
        """Test get stock endpoint converts lowercase symbols to uppercase."""
        # Mock IBKR response
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        # Make request with lowercase symbol
        response = test_client.get("/api/stocks/aapl")

        # Verify IBKR client was called with uppercase
        assert response.status_code == 200
        mock_ibkr_client.get_stock.assert_called_once_with("AAPL")


class TestGetOptionChainEndpoint:
    """Test /api/stocks/{symbol}/chains endpoint."""

    def test_get_option_chain(self, test_client, mock_ibkr_client):
        """Test get option chain endpoint returns OptionChainResponse format."""
        # Mock stock response
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        # Mock option chain response
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_put = OptionContract(
            conid=123457,
            strike=150.0,
            right="P",
            expiration=date(2026, 1, 16),
            bid=1.80,
            ask=1.85,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[mock_put],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Make request
        response = test_client.get("/api/stocks/AAPL/chains?month=JAN26")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["expiration"] == "2026-01-16"
        assert len(data["calls"]) == 1
        assert len(data["puts"]) == 1

        # Verify call data
        call = data["calls"][0]
        assert call["conid"] == 123456
        assert call["strike"] == 150.0
        assert call["right"] == "C"
        assert call["bid"] == 2.50
        assert call["ask"] == 2.55
        assert call["multiplier"] == 100

        # Verify put data
        put = data["puts"][0]
        assert put["conid"] == 123457
        assert put["strike"] == 150.0
        assert put["right"] == "P"
        assert put["bid"] == 1.80
        assert put["ask"] == 1.85

        # Verify IBKR client calls
        mock_ibkr_client.get_stock.assert_called_once_with("AAPL")
        mock_ibkr_client.get_option_chain.assert_called_once_with(265598, "JAN26")

    def test_get_option_chain_uppercase_month(self, test_client, mock_ibkr_client):
        """Test option chain endpoint converts month to uppercase."""
        # Mock responses
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[],
            puts=[],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Make request with lowercase month
        response = test_client.get("/api/stocks/AAPL/chains?month=jan26")

        # Verify month was converted to uppercase
        assert response.status_code == 200
        mock_ibkr_client.get_option_chain.assert_called_once_with(265598, "JAN26")


class TestHealthCheckEndpoint:
    """Test /health endpoint."""

    def test_health_check(self, test_client):
        """Test health check endpoint response format."""
        response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"


class TestAddPositionEndpoint:
    """Test POST /api/strategy/positions endpoint."""

    def _create_session_with_strategy(self, test_client, mock_ibkr_client):
        """Helper to create a session with initialized strategy."""
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)

        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert response.status_code == 200
        return response.cookies.get("session_id")

    def test_add_position_success(self, test_client, mock_ibkr_client):
        """Test successfully adding a position to the strategy."""
        # Create session with strategy
        session_id = self._create_session_with_strategy(test_client, mock_ibkr_client)

        # Mock option chain response
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Add position
        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 1
        position = data["positions"][0]
        assert position["conid"] == 123456
        assert position["strike"] == 150.0
        assert position["right"] == "C"
        assert position["expiration"] == "2026-01-16"
        assert position["quantity"] == 2
        assert position["bid"] == 2.50
        assert position["ask"] == 2.55

    def test_add_position_negative_quantity(self, test_client, mock_ibkr_client):
        """Test adding a short position (negative quantity)."""
        session_id = self._create_session_with_strategy(test_client, mock_ibkr_client)

        mock_put = OptionContract(
            conid=123457,
            strike=145.0,
            right="P",
            expiration=date(2026, 1, 16),
            bid=1.80,
            ask=1.85,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[],
            puts=[mock_put],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["positions"][0]["quantity"] == -1

    def test_add_position_zero_quantity(self, test_client, mock_ibkr_client):
        """Test that adding a position with zero quantity returns 400."""
        session_id = self._create_session_with_strategy(test_client, mock_ibkr_client)

        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 0},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "INVALID_QUANTITY" in data["code"]

    def test_add_position_no_session(self, test_client):
        """Test that adding a position without a session returns 401."""
        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2}
        )

        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    def test_add_position_no_strategy_initialized(self, test_client, session_service):
        """Test that adding a position without initializing strategy returns 400."""
        # Create a session but don't initialize strategy
        session = session_service.create_session()

        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session.session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "No strategy initialized" in data["error"]

    def test_add_position_contract_not_found(self, test_client, mock_ibkr_client):
        """Test that adding a non-existent contract returns 400."""
        session_id = self._create_session_with_strategy(test_client, mock_ibkr_client)

        # Mock option chain with different contracts
        mock_call = OptionContract(
            conid=999999,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Try to add a contract that doesn't exist
        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "CONTRACT_NOT_FOUND" in data["code"]
        assert "not found in option chain" in data["error"]

    def test_add_position_mixed_expiration(self, test_client, mock_ibkr_client):
        """Test that adding positions with mixed expirations returns 400."""
        session_id = self._create_session_with_strategy(test_client, mock_ibkr_client)

        # Add first position with JAN26 expiration
        mock_call1 = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain1 = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call1],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain1)

        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )
        assert response.status_code == 200

        # Try to add second position with FEB26 expiration
        mock_call2 = OptionContract(
            conid=123457,
            strike=150.0,
            right="C",
            expiration=date(2026, 2, 20),
            bid=3.50,
            ask=3.55,
            multiplier=100,
        )
        mock_chain2 = OptionChain(
            expiration=date(2026, 1, 16),  # Still JAN26 for target_date
            calls=[mock_call1, mock_call2],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain2)

        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": 1},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "MIXED_EXPIRATION" in data["code"]

    def test_add_multiple_positions_same_expiration(self, test_client, mock_ibkr_client):
        """Test adding multiple positions with the same expiration succeeds."""
        session_id = self._create_session_with_strategy(test_client, mock_ibkr_client)

        # Mock option chain with multiple contracts
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_put = OptionContract(
            conid=123457,
            strike=145.0,
            right="P",
            expiration=date(2026, 1, 16),
            bid=1.80,
            ask=1.85,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[mock_put],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Add first position
        response1 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )
        assert response1.status_code == 200
        assert len(response1.json()["positions"]) == 1

        # Add second position with same expiration
        response2 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies={"session_id": session_id}
        )
        assert response2.status_code == 200
        data = response2.json()
        assert len(data["positions"]) == 2
        assert data["positions"][0]["conid"] == 123456
        assert data["positions"][1]["conid"] == 123457

    def test_add_duplicate_position(self, test_client, mock_ibkr_client):
        """Test that adding the same conid twice returns 400."""
        session_id = self._create_session_with_strategy(test_client, mock_ibkr_client)

        # Mock option chain
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Add first position
        response1 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )
        assert response1.status_code == 200
        assert len(response1.json()["positions"]) == 1

        # Try to add the same position again
        response2 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 3},
            cookies={"session_id": session_id}
        )
        assert response2.status_code == 400
        data = response2.json()
        assert "DUPLICATE_POSITION" in data["code"]
        assert "already exists" in data["error"]
        assert "PATCH" in data["error"]


class TestModifyPositionEndpoint:
    """Test PATCH /api/strategy/positions/{conid} endpoint."""

    def _create_session_with_position(self, test_client, mock_ibkr_client):
        """Helper to create a session with a position."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add a position
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        return session_id

    def test_modify_position_success(self, test_client, mock_ibkr_client):
        """Test successfully modifying a position's quantity."""
        session_id = self._create_session_with_position(test_client, mock_ibkr_client)

        response = test_client.patch(
            "/api/strategy/positions/123456",
            json={"quantity": 5},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 1
        assert data["positions"][0]["conid"] == 123456
        assert data["positions"][0]["quantity"] == 5

    def test_modify_position_to_negative(self, test_client, mock_ibkr_client):
        """Test modifying a long position to short (flip direction)."""
        session_id = self._create_session_with_position(test_client, mock_ibkr_client)

        response = test_client.patch(
            "/api/strategy/positions/123456",
            json={"quantity": -3},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["positions"][0]["quantity"] == -3

    def test_modify_position_zero_quantity(self, test_client, mock_ibkr_client):
        """Test that modifying to zero quantity returns 400."""
        session_id = self._create_session_with_position(test_client, mock_ibkr_client)

        response = test_client.patch(
            "/api/strategy/positions/123456",
            json={"quantity": 0},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "INVALID_QUANTITY" in data["code"]

    def test_modify_position_not_found(self, test_client, mock_ibkr_client):
        """Test that modifying a non-existent position returns 400."""
        session_id = self._create_session_with_position(test_client, mock_ibkr_client)

        response = test_client.patch(
            "/api/strategy/positions/999999",
            json={"quantity": 5},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "POSITION_NOT_FOUND" in data["code"]
        assert "not found" in data["error"]

    def test_modify_position_no_session(self, test_client):
        """Test that modifying without a session returns 401."""
        response = test_client.patch(
            "/api/strategy/positions/123456",
            json={"quantity": 5}
        )

        assert response.status_code == 401

    def test_modify_position_no_strategy(self, test_client, session_service):
        """Test that modifying without initialized strategy returns 400."""
        session = session_service.create_session()

        response = test_client.patch(
            "/api/strategy/positions/123456",
            json={"quantity": 5},
            cookies={"session_id": session.session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "No strategy initialized" in data["error"]


class TestDeletePositionEndpoint:
    """Test DELETE /api/strategy/positions/{conid} endpoint."""

    def _create_session_with_positions(self, test_client, mock_ibkr_client, count=2):
        """Helper to create a session with multiple positions."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add positions
        contracts = [
            OptionContract(
                conid=123456 + i,
                strike=150.0 + (i * 5),
                right="C" if i % 2 == 0 else "P",
                expiration=date(2026, 1, 16),
                bid=2.50,
                ask=2.55,
                multiplier=100,
            )
            for i in range(count)
        ]

        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[c for c in contracts if c.right == "C"],
            puts=[c for c in contracts if c.right == "P"],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        for i in range(count):
            test_client.post(
                "/api/strategy/positions",
                json={"conid": 123456 + i, "quantity": i + 1},
                cookies={"session_id": session_id}
            )

        return session_id

    def test_delete_position_success(self, test_client, mock_ibkr_client):
        """Test successfully deleting a position."""
        session_id = self._create_session_with_positions(test_client, mock_ibkr_client, count=2)

        response = test_client.delete(
            "/api/strategy/positions/123456",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 1
        assert data["positions"][0]["conid"] == 123457

    def test_delete_last_position(self, test_client, mock_ibkr_client):
        """Test deleting the last position returns empty list."""
        session_id = self._create_session_with_positions(test_client, mock_ibkr_client, count=1)

        response = test_client.delete(
            "/api/strategy/positions/123456",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 0

    def test_delete_position_not_found(self, test_client, mock_ibkr_client):
        """Test that deleting a non-existent position returns 400."""
        session_id = self._create_session_with_positions(test_client, mock_ibkr_client, count=1)

        response = test_client.delete(
            "/api/strategy/positions/999999",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "POSITION_NOT_FOUND" in data["code"]

    def test_delete_position_no_session(self, test_client):
        """Test that deleting without a session returns 401."""
        response = test_client.delete("/api/strategy/positions/123456")

        assert response.status_code == 401

    def test_delete_position_no_strategy(self, test_client, session_service):
        """Test that deleting without initialized strategy returns 400."""
        session = session_service.create_session()

        response = test_client.delete(
            "/api/strategy/positions/123456",
            cookies={"session_id": session.session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "No strategy initialized" in data["error"]

    def test_delete_middle_position(self, test_client, mock_ibkr_client):
        """Test deleting a position from the middle of the list."""
        session_id = self._create_session_with_positions(test_client, mock_ibkr_client, count=3)

        response = test_client.delete(
            "/api/strategy/positions/123457",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 2
        assert data["positions"][0]["conid"] == 123456
        assert data["positions"][1]["conid"] == 123458


class TestResetStrategyEndpoint:
    """Test POST /api/strategy/reset endpoint."""

    def _create_session_with_positions(self, test_client, mock_ibkr_client, count=2):
        """Helper to create a session with multiple positions."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26", "FEB26", "MAR26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add positions
        contracts = [
            OptionContract(
                conid=123456 + i,
                strike=150.0 + (i * 5),
                right="C",
                expiration=date(2026, 1, 16),
                bid=2.50,
                ask=2.55,
                multiplier=100,
            )
            for i in range(count)
        ]

        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=contracts,
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        for i in range(count):
            test_client.post(
                "/api/strategy/positions",
                json={"conid": 123456 + i, "quantity": i + 1},
                cookies={"session_id": session_id}
            )

        return session_id

    def test_reset_strategy_success(self, test_client, mock_ibkr_client):
        """Test successfully resetting a strategy with positions."""
        session_id = self._create_session_with_positions(test_client, mock_ibkr_client, count=3)

        # Verify positions exist before reset
        response = test_client.get("/api/strategy", cookies={"session_id": session_id})
        assert len(response.json()["positions"]) == 3

        # Reset strategy
        response = test_client.post("/api/strategy/reset", cookies={"session_id": session_id})

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert data["current_price"] == 150.25
        assert data["target_date"] == "JAN26"  # Reset to earliest expiration
        assert data["available_expirations"] == ["JAN26", "FEB26", "MAR26"]
        assert data["positions"] == []

        # Verify positions were cleared by fetching strategy again
        response = test_client.get("/api/strategy", cookies={"session_id": session_id})
        assert response.status_code == 200
        assert len(response.json()["positions"]) == 0

    def test_reset_strategy_no_positions(self, test_client, mock_ibkr_client):
        """Test resetting a strategy that has no positions."""
        # Initialize strategy without positions
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["FEB26", "MAR26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Reset strategy
        response = test_client.post("/api/strategy/reset", cookies={"session_id": session_id})

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert data["target_date"] == "FEB26"
        assert data["positions"] == []

    def test_reset_strategy_no_session(self, test_client):
        """Test that resetting without a session returns 401."""
        response = test_client.post("/api/strategy/reset")

        assert response.status_code == 401

    def test_reset_strategy_no_strategy(self, test_client, session_service):
        """Test that resetting without initialized strategy returns 400."""
        session = session_service.create_session()

        response = test_client.post(
            "/api/strategy/reset",
            cookies={"session_id": session.session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "No strategy initialized" in data["error"]

    def test_reset_keeps_same_session(self, test_client, mock_ibkr_client):
        """Test that reset keeps the same session ID."""
        session_id = self._create_session_with_positions(test_client, mock_ibkr_client, count=2)

        # Reset strategy
        response = test_client.post("/api/strategy/reset", cookies={"session_id": session_id})

        assert response.status_code == 200

        # Verify same session still works
        response = test_client.get("/api/strategy", cookies={"session_id": session_id})
        assert response.status_code == 200


class TestGetStrategySummaryEndpoint:
    """Test GET /api/strategy endpoint."""

    def _create_session_with_position(self, test_client, mock_ibkr_client):
        """Helper to create a session with a strategy and position."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add a position
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        return session_id

    def test_get_strategy_summary_success(self, test_client, mock_ibkr_client):
        """Test successfully retrieving strategy summary."""
        session_id = self._create_session_with_position(test_client, mock_ibkr_client)

        # Get strategy summary
        response = test_client.get(
            "/api/strategy",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()

        # Verify structure
        assert data["symbol"] == "AAPL"
        assert data["current_price"] == 150.0
        assert data["target_date"] == "JAN26"
        assert data["available_expirations"] == ["JAN26", "FEB26"]
        assert len(data["positions"]) == 1

        # Verify position details
        position = data["positions"][0]
        assert position["conid"] == 123456
        assert position["strike"] == 150.0
        assert position["right"] == "C"
        assert position["quantity"] == 2
        assert position["bid"] == 2.50
        assert position["ask"] == 2.55

    def test_get_strategy_summary_no_positions(self, test_client, mock_ibkr_client):
        """Test retrieving strategy summary with no positions."""
        # Initialize strategy but don't add positions
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Get summary
        response = test_client.get(
            "/api/strategy",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert len(data["positions"]) == 0

    def test_get_strategy_summary_no_session(self, test_client):
        """Test that getting summary without a session returns 401."""
        response = test_client.get("/api/strategy")

        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    def test_get_strategy_summary_no_strategy_initialized(self, test_client, session_service):
        """Test that getting summary without initialized strategy returns 400."""
        # Create a session but don't initialize strategy
        session = session_service.create_session()

        response = test_client.get(
            "/api/strategy",
            cookies={"session_id": session.session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "No strategy initialized" in data["error"]

    def test_get_strategy_summary_with_multiple_positions(self, test_client, mock_ibkr_client):
        """Test retrieving strategy with multiple positions."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add multiple positions
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_put = OptionContract(
            conid=123457,
            strike=145.0,
            right="P",
            expiration=date(2026, 1, 16),
            bid=1.80,
            ask=1.85,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[mock_put],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Add call
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        # Add put
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies={"session_id": session_id}
        )

        # Get summary
        response = test_client.get(
            "/api/strategy",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 2
        assert data["positions"][0]["conid"] == 123456
        assert data["positions"][1]["conid"] == 123457


class TestUpdateTargetDateEndpoint:
    """Test PATCH /api/strategy/target-date endpoint."""

    def test_update_target_date_success(self, test_client, mock_ibkr_client):
        """Test successfully updating target date with no positions."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26", "FEB26", "MAR26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")
        assert response.json()["target_date"] == "JAN26"

        # Update target date
        response = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "FEB26"},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"
        assert data["target_date"] == "FEB26"
        assert data["available_expirations"] == ["JAN26", "FEB26", "MAR26"]
        assert data["session_id"] == session_id

    def test_update_target_date_no_session(self, test_client):
        """Test that updating target date without session returns 401."""
        response = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "FEB26"}
        )

        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    def test_update_target_date_no_strategy(self, test_client, session_service):
        """Test that updating without initialized strategy returns 400."""
        session = session_service.create_session()

        response = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "FEB26"},
            cookies={"session_id": session.session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "No strategy initialized" in data["error"]

    def test_update_target_date_invalid_date(self, test_client, mock_ibkr_client):
        """Test that invalid target date returns 400."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Try to update to invalid date
        response = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "DEC26"},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "INVALID_TARGET_DATE" in data["code"]
        assert "DEC26" in data["error"]

    def test_update_target_date_with_positions(self, test_client, mock_ibkr_client):
        """Test that updating target date is rejected when positions exist."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add a position
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        # Try to update target date
        response = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "FEB26"},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "POSITIONS_EXIST" in data["code"]
        assert "Cannot change target date" in data["error"]
        assert "delete" in data["error"].lower()

    def test_update_target_date_same_date(self, test_client, mock_ibkr_client):
        """Test updating to the same target date (should succeed as no-op)."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26", "FEB26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")
        assert response.json()["target_date"] == "JAN26"

        # Update to same date
        response = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "JAN26"},
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["target_date"] == "JAN26"


class TestAnalyzeStrategyEndpoint:
    """Test POST /api/strategy/analyze endpoint."""

    def _create_session_with_position(self, test_client, mock_ibkr_client):
        """Helper to create a session with a strategy and position."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add a position - use a future expiration date so analysis doesn't fail
        future_expiration = date.today() + timedelta(days=90)
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=future_expiration,
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=future_expiration,
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        return session_id

    def test_analyze_strategy_success(self, test_client, mock_ibkr_client):
        """Test successful strategy analysis with Monte Carlo simulation."""
        session_id = self._create_session_with_position(test_client, mock_ibkr_client)

        # Mock historical data response
        # Create synthetic price data that trends upward
        closes = [{"date": f"2023-{i//30+1:02d}-{i%30+1:02d}", "close": 100.0 + i * 0.5}
                  for i in range(260)]  # ~1 year of daily data
        mock_ibkr_client.get_historical_data = AsyncMock(
            return_value={"symbol": "AAPL", "closes": closes}
        )

        # Make analysis request
        response = test_client.post(
            "/api/strategy/analyze",
            cookies={"session_id": session_id}
        )

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Verify structure
        assert "price_distribution" in data
        assert "expected_value" in data
        assert "probability_of_profit" in data
        assert "max_gain" in data
        assert "max_loss" in data

        # Verify price distribution
        bins = data["price_distribution"]
        assert len(bins) > 0
        assert all("lower" in b for b in bins)
        assert all("upper" in b for b in bins)
        assert all("count" in b for b in bins)
        assert all("midpoint" in b for b in bins)

        # Verify total count equals bootstrap samples (10k)
        total_count = sum(b["count"] for b in bins)
        assert total_count == 10000

        # Verify probability_of_profit is between 0 and 1
        assert 0.0 <= data["probability_of_profit"] <= 1.0

        # Verify expected_value is a number
        assert isinstance(data["expected_value"], (int, float))

    def test_analyze_strategy_no_session(self, test_client):
        """Test that analyzing without a session returns 401."""
        response = test_client.post("/api/strategy/analyze")

        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    def test_analyze_strategy_no_strategy_initialized(self, test_client, session_service):
        """Test that analyzing without initialized strategy returns 400."""
        # Create a session but don't initialize strategy
        session = session_service.create_session()

        response = test_client.post(
            "/api/strategy/analyze",
            cookies={"session_id": session.session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "No strategy initialized" in data["error"]

    def test_analyze_strategy_no_positions(self, test_client, mock_ibkr_client):
        """Test that analyzing a strategy with no positions returns 400."""
        # Initialize strategy but don't add positions
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Try to analyze without positions
        response = test_client.post(
            "/api/strategy/analyze",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "no option positions" in data["error"].lower()

    def test_analyze_strategy_missing_bid_ask(self, test_client, mock_ibkr_client, session_service):
        """Test that analyzing with missing bid/ask prices returns 400."""
        # Create session with strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add position with missing bid/ask
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 1, 16),
            bid=None,  # Missing bid
            ask=None,  # Missing ask
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 1, 16),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies={"session_id": session_id}
        )

        # Try to analyze
        response = test_client.post(
            "/api/strategy/analyze",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 400
        data = response.json()
        assert "MISSING_BID_ASK" in data["code"]

    def test_analyze_strategy_with_put_spread(self, test_client, mock_ibkr_client):
        """Test analyzing a put spread strategy (long put + short put)."""
        # Initialize strategy
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.0,
            conid=265598,
            available_expirations=["JAN26"],
        )
        mock_ibkr_client.get_stock = AsyncMock(return_value=mock_stock)
        response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = response.cookies.get("session_id")

        # Add put spread positions - use a future expiration date so analysis doesn't fail
        future_expiration = date.today() + timedelta(days=90)
        long_put = OptionContract(
            conid=123456,
            strike=150.0,
            right="P",
            expiration=future_expiration,
            bid=4.50,
            ask=4.55,
            multiplier=100,
        )
        short_put = OptionContract(
            conid=123457,
            strike=145.0,
            right="P",
            expiration=future_expiration,
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=future_expiration,
            calls=[],
            puts=[long_put, short_put],
        )
        mock_ibkr_client.get_option_chain = AsyncMock(return_value=mock_chain)

        # Add long put
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
            cookies={"session_id": session_id}
        )

        # Add short put
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies={"session_id": session_id}
        )

        # Mock historical data
        closes = [{"date": f"2023-{i//30+1:02d}-{i%30+1:02d}", "close": 100.0 + i * 0.5}
                  for i in range(260)]
        mock_ibkr_client.get_historical_data = AsyncMock(
            return_value={"symbol": "AAPL", "closes": closes}
        )

        # Analyze the spread
        response = test_client.post(
            "/api/strategy/analyze",
            cookies={"session_id": session_id}
        )

        assert response.status_code == 200
        data = response.json()

        # Verify analysis results
        assert data["max_gain"] is not None
        assert data["max_loss"] is not None
        assert 0.0 <= data["probability_of_profit"] <= 1.0

        # For a bear put spread, max gain and max loss should be defined
        assert isinstance(data["max_gain"], (int, float))
        assert isinstance(data["max_loss"], (int, float))
