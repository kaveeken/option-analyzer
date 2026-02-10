"""
Integration tests for API workflow.

Tests cover:
- Full strategy workflow: init -> get chain -> add position
- Session persistence across requests
- Session expiration after TTL
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

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
from tests.fixtures.ibkr_responses import make_stock, make_option_chain
from tests.fixtures.fake_ibkr import FakeIBKRClient


@pytest.fixture
def mock_ibkr_client():
    """Create a fake IBKR client with common responses."""
    client = FakeIBKRClient()

    # Pre-configure with simple data for fast integration tests
    client.add_stock("AAPL", make_stock(
        symbol="AAPL",
        current_price=150.25,
        available_expirations=["JAN26", "FEB26"],
    ))

    # Simple chain with one strike for fast tests
    client.add_chain(265598, "JAN26", make_option_chain(
        expiration=date(2026, 1, 16),
        strikes=[150.0],
        base_price=150.25,
        call_conid_start=123456,
        put_conid_start=123457,
    ))

    return client


@pytest.fixture
def session_service():
    """Create a real session service for testing."""
    return SessionService(ttl_seconds=3600)


@pytest.fixture
def test_client(mock_ibkr_client, session_service):
    """Create a test client with mocked dependencies."""
    app = create_app()

    # Create a real thread pool executor for plot operations
    plot_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-plot")

    # Override dependencies
    app.dependency_overrides[get_ibkr_client] = lambda: mock_ibkr_client
    app.dependency_overrides[get_session_service_dep] = lambda: session_service
    app.dependency_overrides[get_plot_executor_dep] = lambda: plot_executor

    yield TestClient(app)

    # Cleanup
    plot_executor.shutdown(wait=True)


class TestFullStrategyWorkflow:
    """Test complete strategy workflow from init to position management."""

    def test_full_strategy_workflow(self, test_client):
        """Test full workflow: init strategy -> get chain -> add position."""
        # Step 1: Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert init_response.status_code == 200
        init_data = init_response.json()
        assert init_data["symbol"] == "AAPL"
        assert init_data["target_date"] == "JAN26"
        session_id = init_data["session_id"]

        # Verify session cookie was set
        assert "session_id" in init_response.cookies
        cookies = {"session_id": session_id}

        # Step 2: Get option chain for target date
        chain_response = test_client.get("/api/stocks/AAPL/chains?month=JAN26")
        assert chain_response.status_code == 200
        chain_data = chain_response.json()
        assert len(chain_data["calls"]) > 0
        call_conid = chain_data["calls"][0]["conid"]

        # Step 3: Add a position
        add_pos_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": call_conid, "quantity": 2},
            cookies=cookies,
        )
        assert add_pos_response.status_code == 200
        positions_data = add_pos_response.json()
        assert len(positions_data["positions"]) == 1
        assert positions_data["positions"][0]["conid"] == call_conid
        assert positions_data["positions"][0]["quantity"] == 2
        assert positions_data["positions"][0]["strike"] == 150.0
        assert positions_data["positions"][0]["right"] == "C"

        # Step 4: Add another position
        put_conid = chain_data["puts"][0]["conid"]
        add_pos2_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": put_conid, "quantity": -1},
            cookies=cookies,
        )
        assert add_pos2_response.status_code == 200
        positions_data2 = add_pos2_response.json()
        assert len(positions_data2["positions"]) == 2
        assert positions_data2["positions"][1]["conid"] == put_conid
        assert positions_data2["positions"][1]["quantity"] == -1

        # Step 5: Modify a position
        modify_response = test_client.patch(
            f"/api/strategy/positions/{call_conid}",
            json={"quantity": 3},
            cookies=cookies,
        )
        assert modify_response.status_code == 200
        modified_data = modify_response.json()
        assert len(modified_data["positions"]) == 2
        # Find the modified position
        modified_pos = next(p for p in modified_data["positions"] if p["conid"] == call_conid)
        assert modified_pos["quantity"] == 3

        # Step 6: Delete a position
        delete_response = test_client.delete(
            f"/api/strategy/positions/{put_conid}",
            cookies=cookies,
        )
        assert delete_response.status_code == 200
        final_data = delete_response.json()
        assert len(final_data["positions"]) == 1
        assert final_data["positions"][0]["conid"] == call_conid

    def test_full_workflow_with_analysis(self, test_client, mock_ibkr_client):
        """Test complete workflow from init to analysis."""
        # Step 1: Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert init_response.status_code == 200
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Step 2: Add a position
        add_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies=cookies,
        )
        assert add_response.status_code == 200

        # Step 3: Add historical data for analysis
        closes = [{"date": f"2023-{i//30+1:02d}-{i%30+1:02d}", "close": 100.0 + i * 0.5}
                  for i in range(260)]  # 1 year of data
        mock_ibkr_client.add_historical(265598, {"symbol": "AAPL", "closes": closes})

        # Step 4: Analyze the strategy
        analyze_response = test_client.post(
            "/api/strategy/analyze",
            cookies=cookies,
        )
        assert analyze_response.status_code == 200
        analysis = analyze_response.json()

        # Verify analysis results
        assert "price_distribution" in analysis
        assert "expected_value" in analysis
        assert "probability_of_profit" in analysis
        assert "max_gain" in analysis
        assert "max_loss" in analysis

        # Verify price distribution bins
        bins = analysis["price_distribution"]
        assert len(bins) > 0
        total_count = sum(b["count"] for b in bins)
        assert total_count == 10000  # 10k Monte Carlo simulations

        # Verify metrics are valid
        assert 0.0 <= analysis["probability_of_profit"] <= 1.0
        assert isinstance(analysis["expected_value"], (int, float))

        # Step 5: Verify session is still valid after analysis
        add_response2 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies=cookies,
        )
        assert add_response2.status_code == 200

    def test_strategy_summary_workflow(self, test_client, mock_ibkr_client):
        """Test workflow with strategy summary endpoint."""
        # Step 1: Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert init_response.status_code == 200
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Step 2: Get summary with no positions
        summary_response = test_client.get("/api/strategy", cookies=cookies)
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["symbol"] == "AAPL"
        assert summary["current_price"] == 150.25
        assert summary["target_date"] == "JAN26"
        assert len(summary["positions"]) == 0

        # Step 3: Add a position
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies=cookies,
        )

        # Step 4: Get summary with one position
        summary_response = test_client.get("/api/strategy", cookies=cookies)
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert len(summary["positions"]) == 1
        assert summary["positions"][0]["conid"] == 123456
        assert summary["positions"][0]["quantity"] == 2

        # Step 5: Add another position
        test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies=cookies,
        )

        # Step 6: Get final summary with two positions
        summary_response = test_client.get("/api/strategy", cookies=cookies)
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert len(summary["positions"]) == 2

        # Verify positions are in order
        assert summary["positions"][0]["conid"] == 123456
        assert summary["positions"][0]["quantity"] == 2
        assert summary["positions"][1]["conid"] == 123457
        assert summary["positions"][1]["quantity"] == -1

    def test_update_target_date_workflow(self, test_client, mock_ibkr_client):
        """Test workflow with updating target date."""
        # Step 1: Initialize strategy with multiple available expirations
        mock_stock = Stock(
            symbol="AAPL",
            current_price=150.25,
            conid=265598,
            available_expirations=["JAN26", "FEB26", "MAR26"],
        )
        mock_ibkr_client.add_stock("AAPL", mock_stock)

        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert init_response.status_code == 200
        session_id = init_response.json()["session_id"]
        assert init_response.json()["target_date"] == "JAN26"
        cookies = {"session_id": session_id}

        # Step 2: Update target date to FEB26 (no positions yet)
        update_response = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "FEB26"},
            cookies=cookies,
        )
        assert update_response.status_code == 200
        assert update_response.json()["target_date"] == "FEB26"

        # Step 3: Add a position for FEB26
        mock_call = OptionContract(
            conid=123456,
            strike=150.0,
            right="C",
            expiration=date(2026, 2, 20),  # FEB26
            bid=2.50,
            ask=2.55,
            multiplier=100,
        )
        mock_chain = OptionChain(
            expiration=date(2026, 2, 20),
            calls=[mock_call],
            puts=[],
        )
        mock_ibkr_client.add_chain(265598, "FEB26", mock_chain)

        add_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 2},
            cookies=cookies,
        )
        assert add_response.status_code == 200

        # Step 4: Try to update target date (should fail - positions exist)
        update_response2 = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "MAR26"},
            cookies=cookies,
        )
        assert update_response2.status_code == 400
        assert "POSITIONS_EXIST" in update_response2.json()["code"]

        # Step 5: Delete position
        delete_response = test_client.delete(
            "/api/strategy/positions/123456",
            cookies=cookies,
        )
        assert delete_response.status_code == 200

        # Step 6: Now update target date should work
        update_response3 = test_client.patch(
            "/api/strategy/target-date",
            json={"target_date": "MAR26"},
            cookies=cookies,
        )
        assert update_response3.status_code == 200
        assert update_response3.json()["target_date"] == "MAR26"


class TestSessionPersistence:
    """Test session persistence across requests."""

    def test_session_persistence_across_requests(self, test_client, session_service):
        """Test that session cookie maintains state across multiple requests."""
        # Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert init_response.status_code == 200
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Verify session exists in service
        session = session_service.get_session(session_id)
        assert "strategy" in session.data
        assert session.data["strategy"]["symbol"] == "AAPL"

        # Add position using session cookie
        add_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
            cookies=cookies,
        )
        assert add_response.status_code == 200

        # Verify session was updated
        session = session_service.get_session(session_id)
        assert len(session.data["strategy"]["positions"]) == 1

        # Make another request with same cookie
        add_response2 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies=cookies,
        )
        assert add_response2.status_code == 200
        positions = add_response2.json()["positions"]
        assert len(positions) == 2

    def test_request_without_session_cookie_fails(self, test_client):
        """Test that requests requiring session fail without cookie."""
        # Try to add position without session cookie
        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
        )

        # Should return 401 (unauthorized) or 400 (bad request)
        assert response.status_code in (400, 401)

    def test_request_with_invalid_session_id_fails(self, test_client):
        """Test that requests with invalid session ID fail."""
        # Try to add position with invalid session cookie
        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
            cookies={"session_id": "invalid-session-id"},
        )

        # Should return 401 (unauthorized) or 400 (bad request)
        assert response.status_code in (400, 401)


class TestSessionExpiration:
    """Test session expiration after TTL."""

    def test_session_expiration(self, mock_ibkr_client):
        """Test that session expires after TTL and returns 401."""
        # Create session service with very short TTL (1 second)
        short_ttl_service = SessionService(ttl_seconds=1)

        # Create test client with short TTL service
        app = create_app()
        app.dependency_overrides[get_ibkr_client] = lambda: mock_ibkr_client
        app.dependency_overrides[get_session_service_dep] = lambda: short_ttl_service
        test_client = TestClient(app)

        # Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert init_response.status_code == 200
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Immediately try to add position (should succeed)
        add_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
            cookies=cookies,
        )
        assert add_response.status_code == 200

        # Wait for session to expire
        time.sleep(1.1)

        # Try to add another position (should fail)
        add_response2 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies=cookies,
        )

        # Should return 401 or 400 due to expired session
        assert add_response2.status_code in (400, 401)

        # Verify session was removed from service
        assert short_ttl_service.session_count() == 0

    def test_session_touch_extends_lifetime(self, mock_ibkr_client):
        """Test that accessing session extends its lifetime."""
        # Create session service with 2-second TTL
        service = SessionService(ttl_seconds=2)

        # Create test client
        app = create_app()
        app.dependency_overrides[get_ibkr_client] = lambda: mock_ibkr_client
        app.dependency_overrides[get_session_service_dep] = lambda: service
        test_client = TestClient(app)

        # Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        assert init_response.status_code == 200
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Wait 1 second
        time.sleep(1)

        # Access session (should extend lifetime)
        add_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
            cookies=cookies,
        )
        assert add_response.status_code == 200

        # Wait another 1 second (total 2 seconds from init, but only 1 from last access)
        time.sleep(1)

        # Should still be valid since we touched it 1 second ago
        add_response2 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123457, "quantity": -1},
            cookies=cookies,
        )
        assert add_response2.status_code == 200

        # Verify session is still in service
        assert service.session_count() == 1


class TestPositionManagement:
    """Test position management edge cases."""

    def test_cannot_add_position_without_strategy(self, test_client, session_service):
        """Test that adding position fails if strategy not initialized."""
        # Create a session without strategy data
        session = session_service.create_session()
        cookies = {"session_id": session.session_id}

        # Try to add position
        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
            cookies=cookies,
        )

        # Should fail with validation error
        assert response.status_code == 400

    def test_cannot_add_zero_quantity(self, test_client):
        """Test that zero quantity is rejected."""
        # Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Try to add position with zero quantity
        response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 0},
            cookies=cookies,
        )

        # Should fail with validation error
        assert response.status_code == 400

    def test_cannot_modify_nonexistent_position(self, test_client):
        """Test that modifying nonexistent position fails."""
        # Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Try to modify nonexistent position
        response = test_client.patch(
            "/api/strategy/positions/999999",
            json={"quantity": 5},
            cookies=cookies,
        )

        # Should fail with validation error
        assert response.status_code == 400

    def test_cannot_delete_nonexistent_position(self, test_client):
        """Test that deleting nonexistent position fails."""
        # Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Try to delete nonexistent position
        response = test_client.delete(
            "/api/strategy/positions/999999",
            cookies=cookies,
        )

        # Should fail with validation error
        assert response.status_code == 400

    def test_mixed_expiration_rejection(self, test_client, mock_ibkr_client):
        """Test that adding positions with different expirations is rejected."""
        # Initialize strategy
        init_response = test_client.post("/api/strategy/init", json={"symbol": "AAPL"})
        session_id = init_response.json()["session_id"]
        cookies = {"session_id": session_id}

        # Add first position (JAN26 expiration - conid 123456)
        add_response = test_client.post(
            "/api/strategy/positions",
            json={"conid": 123456, "quantity": 1},
            cookies=cookies,
        )
        assert add_response.status_code == 200

        # Create a contract with different expiration date
        mock_call_diff = OptionContract(
            conid=789012,
            strike=155.0,
            right="C",
            expiration=date(2026, 2, 20),  # Different expiration!
            bid=3.00,
            ask=3.05,
            multiplier=100,
        )

        # Create modified chain that includes contract with different expiration
        # (simulating API returning wrong data or user trying to mix expirations)
        modified_chain = OptionChain(
            expiration=date(2026, 1, 16),  # Chain says JAN26
            calls=[
                OptionContract(
                    conid=123456,
                    strike=150.0,
                    right="C",
                    expiration=date(2026, 1, 16),
                    bid=2.50,
                    ask=2.55,
                    multiplier=100,
                ),
                mock_call_diff,  # But includes contract with FEB26 expiration
            ],
            puts=[],
        )
        mock_ibkr_client.add_chain(265598, "JAN26", modified_chain)

        # Try to add position with different expiration
        add_response2 = test_client.post(
            "/api/strategy/positions",
            json={"conid": 789012, "quantity": 1},
            cookies=cookies,
        )

        # Should fail with mixed expiration error
        assert add_response2.status_code == 400
        assert "MIXED_EXPIRATION" in add_response2.json()["code"]
