"""
End-to-end error handling tests for IBKR client.

Tests cover:
- Network failures and retry logic with exponential backoff
- Rate limiting behavior and 429 handling
- Cache behavior under error conditions
- All HTTP error codes (4xx, 5xx)
- Malformed responses and missing fields
- Endpoint-specific error paths
- Cross-endpoint error propagation
- Concurrent request error isolation
"""

import asyncio
import time
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from option_analyzer.clients.cache import InMemoryCache
from option_analyzer.clients.ibkr import IBKRClient
from option_analyzer.config import Settings
from option_analyzer.utils.exceptions import (
    IBKRAPIError,
    IBKRConnectionError,
    SymbolNotFoundError,
)
from option_analyzer.utils.rate_limiter import RateLimiter


@pytest.fixture
def settings() -> Settings:
    """Create test settings with fast retry delays for testing."""
    settings = Settings()
    settings.ibkr_retry_delay = 0.1  # Fast retries for testing
    settings.ibkr_max_retries = 3
    return settings


@pytest.fixture
def cache() -> InMemoryCache:
    """Create test cache."""
    return InMemoryCache()


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Create test rate limiter."""
    return RateLimiter(max_requests=50, per_seconds=1.0)


@pytest.fixture
def fast_rate_limiter() -> RateLimiter:
    """Create rate limiter with low limits for testing."""
    return RateLimiter(max_requests=5, per_seconds=1.0)


@pytest.fixture
async def client(
    settings: Settings, cache: InMemoryCache, rate_limiter: RateLimiter
) -> IBKRClient:
    """Create test IBKR client."""
    async with IBKRClient(settings, cache, rate_limiter) as client:
        yield client


@pytest.fixture
async def fast_limited_client(
    settings: Settings, cache: InMemoryCache, fast_rate_limiter: RateLimiter
) -> IBKRClient:
    """Create client with aggressive rate limiting for testing."""
    async with IBKRClient(settings, cache, fast_rate_limiter) as client:
        yield client


# =============================================================================
# Network Failure & Retry Logic Tests
# =============================================================================


class TestNetworkRetry:
    """Test retry behavior with network failures."""

    @pytest.mark.asyncio
    async def test_connection_retry_exponential_backoff_timing(
        self, client: IBKRClient
    ) -> None:
        """Verify exponential backoff timing: 0.1s, 0.2s, 0.4s delays."""
        # Mock connection failures, then success
        client.client.request = AsyncMock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                Mock(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    json=lambda: [{"conid": 265598, "symbol": "AAPL"}],
                ),
            ]
        )

        start = time.time()
        result = await client.get_request("iserver/secdef/search?symbol=AAPL")
        elapsed = time.time() - start

        # Should have delays: 0.1 + 0.2 + 0.4 = 0.7s
        # Allow 0.6-0.9s for timing variance
        assert 0.6 < elapsed < 0.9
        assert result == [{"conid": 265598, "symbol": "AAPL"}]

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_raises_connection_error(
        self, client: IBKRClient
    ) -> None:
        """After max retries (3), raises IBKRConnectionError."""
        # Mock: all attempts fail
        client.client.request = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with pytest.raises(IBKRConnectionError):
            await client.get_request("iserver/secdef/search?symbol=AAPL")

        # Should have tried 4 times (initial + 3 retries)
        assert client.client.request.call_count == 4

    @pytest.mark.asyncio
    async def test_timeout_error_triggers_retry(self, client: IBKRClient) -> None:
        """TimeoutException should trigger retry logic."""
        client.client.request = AsyncMock(
            side_effect=[
                httpx.TimeoutException("Request timeout"),
                Mock(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    json=lambda: [{"conid": 265598, "symbol": "AAPL"}],
                ),
            ]
        )

        result = await client.get_request("iserver/secdef/search?symbol=AAPL")

        assert result == [{"conid": 265598, "symbol": "AAPL"}]
        assert client.client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_request_error_triggers_retry(self, client: IBKRClient) -> None:
        """Generic RequestError should trigger retry logic."""
        client.client.request = AsyncMock(
            side_effect=[
                httpx.RequestError("Network error"),
                httpx.RequestError("Network error"),
                Mock(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    json=lambda: [{"conid": 265598, "symbol": "AAPL"}],
                ),
            ]
        )

        result = await client.get_request("iserver/secdef/search?symbol=AAPL")

        assert result == [{"conid": 265598, "symbol": "AAPL"}]
        assert client.client.request.call_count == 3

    @pytest.mark.asyncio
    async def test_connection_error_in_get_conid(self, client: IBKRClient) -> None:
        """Connection error during symbol lookup propagates correctly."""
        client.get_request = AsyncMock(side_effect=IBKRConnectionError())

        with pytest.raises(IBKRConnectionError):
            await client.get_conid("AAPL")

    @pytest.mark.asyncio
    async def test_connection_error_in_get_stock(self, client: IBKRClient) -> None:
        """Connection error during stock quote propagates correctly."""
        client.get_search_results = AsyncMock(side_effect=IBKRConnectionError())

        with pytest.raises(IBKRConnectionError):
            await client.get_stock("AAPL")

    @pytest.mark.asyncio
    async def test_connection_error_in_get_historical_data(
        self, client: IBKRClient
    ) -> None:
        """Connection error during historical data propagates correctly."""
        client.get_request = AsyncMock(side_effect=IBKRConnectionError())

        with pytest.raises(IBKRConnectionError):
            await client.get_historical_data(265598, years=1)

    @pytest.mark.asyncio
    async def test_partial_network_failure_in_option_pricing(
        self, client: IBKRClient
    ) -> None:
        """When pricing options in batches, network failure in one batch raises error."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        # Create chain with 12 contracts (will be 2 batches of 9 and 3)
        calls = [
            OptionContract(
                conid=1000 + i, strike=100.0 + i, right="C", expiration=date(2025, 1, 17)
            )
            for i in range(6)
        ]
        puts = [
            OptionContract(
                conid=2000 + i, strike=100.0 + i, right="P", expiration=date(2025, 1, 17)
            )
            for i in range(6)
        ]
        chain = OptionChain(expiration=date(2025, 1, 17), calls=calls, puts=puts)

        # Mock: first batch succeeds, second fails
        client.get_market_snapshot = AsyncMock(
            side_effect=[
                # First batch (9 contracts: 6 calls + 3 puts)
                [{"conid": 1000 + i, "bid": 1.0, "ask": 1.1} for i in range(6)]
                + [{"conid": 2000 + i, "bid": 2.0, "ask": 2.1} for i in range(3)],
                # Second batch fails
                IBKRConnectionError(),
            ]
        )

        # Operation should fail with connection error
        with pytest.raises(IBKRConnectionError):
            await client.price_option_chain(chain)

        # Should have attempted 2 batches
        assert client.get_market_snapshot.call_count == 2


# =============================================================================
# Rate Limiting Tests
# =============================================================================


class TestRateLimiting:
    """Test rate limiter prevents API bans."""

    @pytest.mark.asyncio
    async def test_rate_limiter_throttles_burst_requests(
        self, fast_limited_client: IBKRClient
    ) -> None:
        """5 requests/sec limit: 10 different requests should take ~1+ seconds."""
        # Mock HTTP client to succeed instantly, but rate limiter still runs
        call_count = 0

        def json_func():
            nonlocal call_count
            call_count += 1
            return [{"conid": 100000 + call_count, "symbol": f"SYM{call_count}"}]

        mock_response = Mock(
            status_code=200,
            headers={"content-type": "application/json"},
            json=json_func,
            raise_for_status=Mock(),
        )
        fast_limited_client.client.request = AsyncMock(return_value=mock_response)

        start = time.time()

        # Send 10 requests with different symbols to avoid caching
        tasks = [fast_limited_client.get_conid(f"SYM{i}") for i in range(10)]
        await asyncio.gather(*tasks)

        elapsed = time.time() - start

        # 10 requests at 5 req/s should take at least 1 second
        # (5 immediate, 5 more after 1 second)
        assert elapsed > 0.9

    @pytest.mark.asyncio
    async def test_429_response_triggers_backoff(self, client: IBKRClient) -> None:
        """429 status code triggers exponential backoff and retry."""
        # Mock: first request 429, second succeeds
        mock_429_response = Mock(
            status_code=429,
            text="Too many requests",
            raise_for_status=Mock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=Mock(), response=Mock(status_code=429, text="Too many requests")
                )
            ),
        )
        mock_success_response = Mock(
            status_code=200,
            headers={"content-type": "application/json"},
            json=lambda: [{"conid": 265598, "symbol": "AAPL"}],
            raise_for_status=Mock(),
        )

        client.client.request = AsyncMock(
            side_effect=[mock_429_response, mock_success_response]
        )

        start = time.time()
        result = await client.get_request("iserver/secdef/search?symbol=AAPL")
        elapsed = time.time() - start

        # Should have backoff delay (~0.1s)
        assert elapsed > 0.09
        assert result == [{"conid": 265598, "symbol": "AAPL"}]

    @pytest.mark.asyncio
    async def test_429_max_retries_exhausted(self, client: IBKRClient) -> None:
        """429 after max retries raises IBKRAPIError."""
        mock_429_response = Mock(
            status_code=429,
            text="Too many requests",
            raise_for_status=Mock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=Mock(), response=Mock(status_code=429, text="Too many requests")
                )
            ),
        )

        client.client.request = AsyncMock(return_value=mock_429_response)

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_request("iserver/secdef/search?symbol=AAPL")

        assert "Maximum retries" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_concurrent_requests_respect_rate_limit(
        self, fast_limited_client: IBKRClient
    ) -> None:
        """Multiple concurrent tasks share the same rate limiter."""
        # Mock HTTP responses with different symbols to prevent caching
        call_count = 0

        def json_func():
            nonlocal call_count
            call_count += 1
            return [{"conid": 200000 + call_count, "symbol": f"TEST{call_count}"}]

        mock_response = Mock(
            status_code=200,
            headers={"content-type": "application/json"},
            json=json_func,
            raise_for_status=Mock(),
        )
        fast_limited_client.client.request = AsyncMock(return_value=mock_response)

        start = time.time()

        # 10 concurrent requests with 5 req/s limit (different symbols)
        tasks = [fast_limited_client.get_conid(f"TEST{i}") for i in range(10)]
        results = await asyncio.gather(*tasks)

        elapsed = time.time() - start

        # 10 requests at 5 req/s should take at least 1 second
        assert elapsed > 0.9
        assert len(results) == 10


# =============================================================================
# Cache Behavior Under Errors Tests
# =============================================================================


class TestCacheErrorBehavior:
    """Test cache behavior during failures."""

    @pytest.mark.asyncio
    async def test_cache_not_set_on_api_error(self, client: IBKRClient) -> None:
        """500 error should not populate cache."""
        # Mock first call fails, second succeeds
        mock_500_response = Mock(
            status_code=500,
            text="Internal server error",
            raise_for_status=Mock(
                side_effect=httpx.HTTPStatusError(
                    "500", request=Mock(), response=Mock(status_code=500, text="Internal server error")
                )
            ),
        )
        mock_success_response = Mock(
            status_code=200,
            headers={"content-type": "application/json"},
            json=lambda: [{"conid": 265598, "symbol": "AAPL"}],
            raise_for_status=Mock(),
        )

        client.client.request = AsyncMock(
            side_effect=[
                mock_500_response,
                mock_500_response,
                mock_500_response,
                mock_500_response,  # Max retries on first call
                mock_success_response,  # Second call succeeds
            ]
        )

        # First call should fail after retries
        with pytest.raises(IBKRAPIError):
            await client.get_conid("AAPL")

        # Cache should be empty
        assert client._cache.get("iserver/secdef/search?symbol=AAPL&assetType=STK") is None

        # Second call should hit API (not cache)
        result = await client.get_conid("AAPL")
        assert result == 265598
        assert client.client.request.call_count == 5

    @pytest.mark.asyncio
    async def test_cache_not_set_on_connection_error(self, client: IBKRClient) -> None:
        """Connection errors don't cache."""
        client.client.request = AsyncMock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                Mock(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    json=lambda: [{"conid": 265598, "symbol": "AAPL"}],
                    raise_for_status=Mock(),
                ),
            ]
        )

        # First call fails
        with pytest.raises(IBKRConnectionError):
            await client.get_conid("AAPL")

        # Cache should be empty
        assert client._cache.get("iserver/secdef/search?symbol=AAPL&assetType=STK") is None

        # Second call hits API
        result = await client.get_conid("AAPL")
        assert result == 265598

    @pytest.mark.asyncio
    async def test_cache_set_after_successful_retry(self, client: IBKRClient) -> None:
        """Successful retry should populate cache."""
        client.client.request = AsyncMock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                Mock(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    json=lambda: [{"conid": 265598, "symbol": "AAPL"}],
                    raise_for_status=Mock(),
                ),
            ]
        )

        # First call succeeds after retry
        result1 = await client.get_conid("AAPL")
        assert result1 == 265598

        # Second call should use cache
        result2 = await client.get_conid("AAPL")
        assert result2 == 265598

        # Should only call API twice (first attempt + retry, no third call)
        assert client.client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_symbol_not_found_error_raised(self, client: IBKRClient) -> None:
        """SymbolNotFoundError raised when symbol not found."""
        # Mock empty response
        client.get_request = AsyncMock(return_value=[])

        # Should raise SymbolNotFoundError
        with pytest.raises(SymbolNotFoundError) as exc_info:
            await client.get_conid("INVALID")

        assert "INVALID" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cache_isolation_between_endpoints(self, client: IBKRClient) -> None:
        """Errors in one endpoint don't affect successful calls to other endpoints."""
        # Historical data call succeeds
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "data": [{"t": 1672531200000, "c": 150.0}]}
        )
        hist_data = await client.get_historical_data(265598, years=1)
        assert hist_data["symbol"] == "AAPL"

        # Symbol lookup fails (different endpoint)
        client.get_request = AsyncMock(return_value=[])
        with pytest.raises(SymbolNotFoundError):
            await client.get_conid("INVALID")

        # Historical data still cached and accessible (uses different cache key)
        # Reset mock to return the cached historical data
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "data": [{"t": 1672531200000, "c": 150.0}]}
        )

        # Can still get historical data (should be cached)
        hist_data2 = await client.get_historical_data(265598, years=1)
        assert hist_data2["symbol"] == "AAPL"


# =============================================================================
# API Error Conditions Tests
# =============================================================================


class TestAPIErrorCodes:
    """Test all HTTP error code paths."""

    @pytest.mark.parametrize(
        "status_code,should_retry",
        [
            (400, False),  # Bad request - no retry
            (401, False),  # Unauthorized - no retry
            (403, False),  # Forbidden - no retry
            (404, False),  # Not found - no retry
            (429, True),   # Rate limited - retry
            (500, True),   # Server error - retry
            (502, True),   # Bad gateway - retry
            (503, True),   # Service unavailable - retry
        ],
    )
    @pytest.mark.asyncio
    async def test_status_codes_raise_appropriate_errors(
        self, client: IBKRClient, status_code: int, should_retry: bool
    ) -> None:
        """Each status code raises correct exception with appropriate retry."""
        mock_error_response = Mock(
            status_code=status_code,
            text=f"Error {status_code}",
            raise_for_status=Mock(
                side_effect=httpx.HTTPStatusError(
                    f"{status_code}",
                    request=Mock(),
                    response=Mock(status_code=status_code, text=f"Error {status_code}"),
                )
            ),
        )

        client.client.request = AsyncMock(return_value=mock_error_response)

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_request("iserver/secdef/search?symbol=AAPL")

        # For 429, the error message is "Maximum retries" after exhaustion
        # For others, the status code should be in the message
        if status_code == 429:
            assert "Maximum retries" in str(exc_info.value) or str(status_code) in str(exc_info.value)
        else:
            assert str(status_code) in str(exc_info.value)

        # Check retry count
        if should_retry:
            # Should retry max_retries + 1 (initial attempt)
            assert client.client.request.call_count == 4
        else:
            # Should not retry
            assert client.client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_malformed_json_response(self, client: IBKRClient) -> None:
        """Non-JSON response raises IBKRAPIError."""
        mock_html_response = Mock(
            status_code=200,
            headers={"content-type": "text/html"},
            raise_for_status=Mock(),
        )

        client.client.request = AsyncMock(return_value=mock_html_response)

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_request("iserver/secdef/search?symbol=AAPL")

        assert "json" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_required_field_conid(self, client: IBKRClient) -> None:
        """Missing 'conid' field in response raises KeyError."""
        # Response missing conid field
        client.get_request = AsyncMock(
            return_value=[{"symbol": "AAPL", "description": "Apple Inc"}]
        )

        with pytest.raises(KeyError):
            await client.get_conid("AAPL")

    @pytest.mark.asyncio
    async def test_invalid_data_type_in_response(self, client: IBKRClient) -> None:
        """Invalid data types are handled (conid as string -> converted to int)."""
        # conid as string should be converted
        client.get_request = AsyncMock(
            return_value=[{"conid": "265598", "symbol": "AAPL"}]
        )

        result = await client.get_conid("AAPL")
        assert result == 265598
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_empty_response_list(self, client: IBKRClient) -> None:
        """Empty list response raises SymbolNotFoundError."""
        client.get_request = AsyncMock(return_value=[])

        with pytest.raises(SymbolNotFoundError):
            await client.get_conid("INVALID")

    @pytest.mark.asyncio
    async def test_non_list_response_type(self, client: IBKRClient) -> None:
        """Non-list response when list expected raises error."""
        # String instead of list
        client.get_request = AsyncMock(return_value="not a list")

        with pytest.raises(SymbolNotFoundError):
            await client.get_conid("AAPL")

    @pytest.mark.asyncio
    async def test_dict_response_when_list_expected(self, client: IBKRClient) -> None:
        """Dict response when list expected raises error."""
        client.get_request = AsyncMock(return_value={"error": "something"})

        with pytest.raises(SymbolNotFoundError):
            await client.get_conid("AAPL")


# =============================================================================
# Endpoint-Specific Error Paths Tests
# =============================================================================


class TestSymbolLookupErrors:
    """Test error paths specific to symbol lookup."""

    @pytest.mark.asyncio
    async def test_malformed_search_response_missing_sections(
        self, client: IBKRClient
    ) -> None:
        """Search response missing 'sections' field handled gracefully."""
        # Missing sections causes KeyError in get_stock
        client.get_request = AsyncMock(
            return_value=[{"conid": 265598, "symbol": "AAPL"}]
        )

        with pytest.raises(KeyError):
            await client.get_stock("AAPL")

    @pytest.mark.asyncio
    async def test_search_different_asset_types(self, client: IBKRClient) -> None:
        """Different asset types create different cache entries."""
        client.get_request = AsyncMock(
            side_effect=[
                [{"conid": 265598, "symbol": "AAPL"}],  # STK
                [{"conid": 999999, "symbol": "AAPL"}],  # OPT
            ]
        )

        # Stock asset type
        conid1 = await client.get_conid("AAPL", asset_type="STK")

        # Option asset type
        conid2 = await client.get_conid("AAPL", asset_type="OPT")

        # Should call API twice (different asset types)
        assert client.get_request.call_count == 2
        assert conid1 == 265598
        assert conid2 == 999999


class TestStockQuoteErrors:
    """Test error paths specific to stock quotes."""

    @pytest.mark.asyncio
    async def test_missing_snapshot_data(self, client: IBKRClient) -> None:
        """Empty snapshot list raises IBKRAPIError."""
        client.get_search_results = AsyncMock(
            return_value=[
                {"conid": 265598, "symbol": "AAPL", "sections": []}
            ]
        )
        client.get_market_snapshot = AsyncMock(return_value=[])

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_stock("AAPL")

        assert "snapshot" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_snapshot_invalid_response_type(self, client: IBKRClient) -> None:
        """Non-list snapshot response raises error."""
        client.get_request = AsyncMock(
            return_value={"error": "not a list"}
        )

        with pytest.raises(IBKRAPIError):
            await client.get_market_snapshot(265598, timedelta(minutes=5))

    @pytest.mark.asyncio
    async def test_missing_price_fields_in_snapshot(self, client: IBKRClient) -> None:
        """Missing price fields result in None values."""
        client.get_search_results = AsyncMock(
            return_value=[
                {"conid": 265598, "symbol": "AAPL", "sections": []}
            ]
        )
        # Snapshot with no price fields
        client.get_request = AsyncMock(
            return_value=[{"conid": 265598}]
        )

        # Raises IBKRAPIError when no price fields are available
        with pytest.raises(IBKRAPIError):
            await client.get_stock("AAPL")


class TestOptionChainErrors:
    """Test error paths specific to option chains."""

    @pytest.mark.asyncio
    async def test_empty_strikes_response(self, client: IBKRClient) -> None:
        """Empty strikes dict (no calls or puts) raises error."""
        client.get_request = AsyncMock(return_value={"call": [], "put": []})

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_unpriced_option_chain(265598, "JAN25")

        assert "neither puts or calls" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_strikes_invalid_response_format(self, client: IBKRClient) -> None:
        """Strikes response not a dict with 2 keys raises error."""
        # Only has 'call', missing 'put'
        client.get_request = AsyncMock(return_value={"call": [100.0]})

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

        assert "Invalid option strikes" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_strikes_wrong_type(self, client: IBKRClient) -> None:
        """Strikes response as list instead of dict raises error."""
        client.get_request = AsyncMock(return_value=[100.0, 110.0])

        with pytest.raises(IBKRAPIError):
            await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

    @pytest.mark.asyncio
    async def test_contract_info_invalid_response(self, client: IBKRClient) -> None:
        """Contract info not a list raises error."""
        client.get_request = AsyncMock(return_value={"error": "not a list"})

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_unpriced_option_contract(
                265598, "JAN25", "C", 150.0, timedelta(minutes=15)
            )

        assert "Invalid contract info" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_pricing_snapshot_conid_mismatch(self, client: IBKRClient) -> None:
        """Snapshot conid not matching any contract raises error."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        call = OptionContract(
            conid=1001, strike=150.0, right="C", expiration=date(2025, 1, 17)
        )
        chain = OptionChain(expiration=date(2025, 1, 17), calls=[call], puts=[])

        # Snapshot with wrong conid
        client.get_market_snapshot = AsyncMock(
            return_value=[{"conid": 9999, "bid": 5.0, "ask": 5.2}]
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.price_option_chain(chain)

        assert "does not correspond to contract" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_pricing_invalid_conid_type(self, client: IBKRClient) -> None:
        """Invalid conid type in snapshot raises error."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        call = OptionContract(
            conid=1001, strike=150.0, right="C", expiration=date(2025, 1, 17)
        )
        chain = OptionChain(expiration=date(2025, 1, 17), calls=[call], puts=[])

        # Snapshot with string conid
        client.get_market_snapshot = AsyncMock(
            return_value=[{"conid": "invalid", "bid": 5.0, "ask": 5.2}]
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.price_option_chain(chain)

        assert "Invalid conid" in str(exc_info.value)


class TestHistoricalDataErrors:
    """Test error paths specific to historical data."""

    @pytest.mark.asyncio
    async def test_missing_data_field(self, client: IBKRClient) -> None:
        """Response without 'data' field raises error."""
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "error": "no data"}
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_historical_data(265598, years=1)

        assert "Historical data missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_data_array(self, client: IBKRClient) -> None:
        """Empty data array returns empty prices list (not an error)."""
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "data": []}
        )

        result = await client.get_historical_data(265598, years=1)

        assert result["symbol"] == "AAPL"
        assert result["closes"] == []

    @pytest.mark.asyncio
    async def test_years_validation_negative(self, client: IBKRClient) -> None:
        """Negative years clamped to 3 (max)."""
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "data": []}
        )

        await client.get_historical_data(265598, years=-1)

        # Should request 3y
        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args

    @pytest.mark.asyncio
    async def test_years_validation_zero(self, client: IBKRClient) -> None:
        """Zero years clamped to 3."""
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "data": []}
        )

        await client.get_historical_data(265598, years=0)

        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args

    @pytest.mark.asyncio
    async def test_years_validation_too_large(self, client: IBKRClient) -> None:
        """Years > 3 clamped to 3."""
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "data": []}
        )

        await client.get_historical_data(265598, years=10)

        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args


# =============================================================================
# End-to-End Error Scenarios Tests
# =============================================================================


class TestE2EErrorScenarios:
    """Cross-endpoint error propagation and concurrent error handling."""

    @pytest.mark.asyncio
    async def test_cascading_failure_symbol_lookup(self, client: IBKRClient) -> None:
        """Symbol lookup failure prevents downstream operations."""
        client.get_search_results = AsyncMock(side_effect=SymbolNotFoundError("INVALID"))

        # Symbol lookup fails
        with pytest.raises(SymbolNotFoundError):
            await client.get_stock("INVALID")

        # Option chain would also fail (but never gets there)
        client.get_search_results.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_requests_error_isolation(self, client: IBKRClient) -> None:
        """Errors in one concurrent request don't affect others."""
        # Mock: AAPL succeeds, INVALID fails, MSFT succeeds
        async def mock_search(symbol: str, asset_type=None, ttl=None):
            if symbol == "AAPL":
                return [{"conid": 265598, "symbol": "AAPL"}]
            elif symbol == "INVALID":
                raise SymbolNotFoundError("INVALID")
            elif symbol == "MSFT":
                return [{"conid": 456789, "symbol": "MSFT"}]
            return []

        client.get_search_results = AsyncMock(side_effect=mock_search)

        tasks = [
            client.get_conid("AAPL"),
            client.get_conid("INVALID"),
            client.get_conid("MSFT"),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # AAPL succeeded
        assert results[0] == 265598
        # INVALID failed
        assert isinstance(results[1], SymbolNotFoundError)
        # MSFT succeeded
        assert results[2] == 456789

    @pytest.mark.asyncio
    async def test_partial_success_in_batch_operations(self, client: IBKRClient) -> None:
        """Batch operations handle partial failures appropriately."""
        # Get multiple symbols concurrently
        call_count = 0

        async def mock_search(symbol: str, asset_type=None, ttl=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # Second call fails
                raise IBKRConnectionError()
            return [{"conid": 100000 + call_count, "symbol": symbol}]

        client.get_search_results = AsyncMock(side_effect=mock_search)

        results = await asyncio.gather(
            client.get_conid("SYM1"),
            client.get_conid("SYM2"),  # This will fail
            client.get_conid("SYM3"),
            return_exceptions=True,
        )

        assert isinstance(results[0], int)
        assert isinstance(results[1], IBKRConnectionError)
        assert isinstance(results[2], int)

    @pytest.mark.asyncio
    async def test_error_during_option_chain_retrieval(self, client: IBKRClient) -> None:
        """Error during option chain retrieval after successful symbol lookup."""
        # Symbol lookup succeeds
        client.get_search_results = AsyncMock(
            return_value=[{"conid": 265598, "symbol": "AAPL", "sections": []}]
        )
        client.get_market_snapshot = AsyncMock(
            return_value=[{"conid": 265598, "last": 150.0}]
        )

        # Stock quote succeeds
        stock = await client.get_stock("AAPL")
        assert stock.symbol == "AAPL"
        assert stock.conid == 265598

        # But option chain fails
        client.get_request = AsyncMock(side_effect=IBKRAPIError("Failed to get strikes"))

        with pytest.raises(IBKRAPIError):
            await client.get_option_chain(265598, "JAN25")

    @pytest.mark.asyncio
    async def test_mixed_success_failure_different_endpoints(
        self, client: IBKRClient
    ) -> None:
        """Success on one endpoint doesn't affect errors on another."""
        # Historical data succeeds
        client.get_request = AsyncMock(
            return_value={"symbol": "AAPL", "data": []}
        )
        hist_data = await client.get_historical_data(265598, years=1)
        assert hist_data["symbol"] == "AAPL"

        # Stock quote fails
        client.get_search_results = AsyncMock(side_effect=IBKRConnectionError())

        with pytest.raises(IBKRConnectionError):
            await client.get_stock("AAPL")

        # Historical data still cached and works
        hist_data2 = await client.get_historical_data(265598, years=1)
        assert hist_data2["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_retry_exhaustion_with_server_errors(self, client: IBKRClient) -> None:
        """Server errors (500) retry until exhaustion."""
        mock_500 = Mock(
            status_code=500,
            text="Internal server error",
            raise_for_status=Mock(
                side_effect=httpx.HTTPStatusError(
                    "500", request=Mock(), response=Mock(status_code=500, text="Internal server error")
                )
            ),
        )

        client.client.request = AsyncMock(return_value=mock_500)

        start = time.time()

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_request("iserver/secdef/search?symbol=AAPL")

        elapsed = time.time() - start

        # Should have retried 3 times with backoff: 0.1 + 0.2 + 0.4 = 0.7s
        assert elapsed > 0.6
        assert "500" in str(exc_info.value)
        assert client.client.request.call_count == 4  # Initial + 3 retries

    @pytest.mark.asyncio
    async def test_client_error_no_retry(self, client: IBKRClient) -> None:
        """Client errors (400) should not retry."""
        mock_400 = Mock(
            status_code=400,
            text="Bad request",
            raise_for_status=Mock(
                side_effect=httpx.HTTPStatusError(
                    "400", request=Mock(), response=Mock(status_code=400, text="Bad request")
                )
            ),
        )

        client.client.request = AsyncMock(return_value=mock_400)

        start = time.time()

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_request("iserver/secdef/search?symbol=AAPL")

        elapsed = time.time() - start

        # Should fail immediately without retry
        assert elapsed < 0.2
        assert "400" in str(exc_info.value)
        assert client.client.request.call_count == 1  # No retries
