"""
Unit tests for IBKR client.

Tests cover:
- Symbol lookup (get_conid) with caching
- Success cases (single match)
- Error cases (not found, ambiguous, API failures)
- Cache integration and TTL
- Asset type parameter handling
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

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
    """Create test settings."""
    return Settings()


@pytest.fixture
def cache() -> InMemoryCache:
    """Create test cache."""
    return InMemoryCache()


@pytest.fixture
def rate_limiter() -> RateLimiter:
    """Create test rate limiter."""
    return RateLimiter(max_requests=50, per_seconds=1.0)


@pytest.fixture
async def client(
    settings: Settings, cache: InMemoryCache, rate_limiter: RateLimiter
) -> IBKRClient:
    """Create test IBKR client."""
    async with IBKRClient(settings, cache, rate_limiter) as client:
        yield client


class TestGetConid:
    """Test get_conid symbol lookup method."""

    @pytest.mark.asyncio
    async def test_get_conid_success(self, client: IBKRClient) -> None:
        """Test successful symbol lookup returns conid."""
        # Mock successful API response
        client.get_request = AsyncMock(
            return_value=[
                {"conid": 265598, "symbol": "AAPL", "description": "Apple Inc"}
            ]
        )

        conid = await client.get_conid("AAPL")

        assert conid == 265598
        assert isinstance(conid, int)

    @pytest.mark.asyncio
    async def test_get_conid_uses_cache(self, client: IBKRClient) -> None:
        """Test that symbol lookup uses cache on second call."""
        mock_response = [{"conid": 265598, "symbol": "AAPL"}]
        client.get_request = AsyncMock(return_value=mock_response)

        # First call - hits API
        conid1 = await client.get_conid("AAPL")

        # Second call - should use cache
        conid2 = await client.get_conid("AAPL")

        # Should only call API once
        assert client.get_request.call_count == 1
        assert conid1 == conid2 == 265598

    @pytest.mark.asyncio
    async def test_get_conid_cache_ttl_24h(self, client: IBKRClient) -> None:
        """Test that cache uses 24-hour TTL."""
        with patch.object(client._cache, "set") as mock_cache_set:
            client.get_request = AsyncMock(
                return_value=[{"conid": 265598, "symbol": "AAPL"}]
            )

            await client.get_conid("AAPL")

            # Verify cache.set was called with 24h TTL
            assert mock_cache_set.called
            call_args = mock_cache_set.call_args
            # Third argument should be the TTL
            assert call_args[0][2] == timedelta(hours=24)

    @pytest.mark.asyncio
    async def test_get_conid_symbol_not_found_empty_list(
        self, client: IBKRClient
    ) -> None:
        """Test that empty results raise SymbolNotFoundError."""
        # Mock empty response
        client.get_request = AsyncMock(return_value=[])

        with pytest.raises(SymbolNotFoundError) as exc_info:
            await client.get_conid("INVALID")

        assert "INVALID" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_conid_invalid_response_format(self, client: IBKRClient) -> None:
        """Test that malformed API response raises SymbolNotFoundError."""
        # Mock malformed response (not a list of dicts)
        client.get_request = AsyncMock(return_value={"error": "bad format"})

        with pytest.raises(SymbolNotFoundError):
            await client.get_conid("AAPL")

    @pytest.mark.asyncio
    async def test_get_conid_invalid_response_not_list(
        self, client: IBKRClient
    ) -> None:
        """Test that non-list response raises SymbolNotFoundError."""
        # Mock response that's not a list
        client.get_request = AsyncMock(return_value="not a list")

        with pytest.raises(SymbolNotFoundError):
            await client.get_conid("AAPL")

    @pytest.mark.asyncio
    async def test_get_conid_missing_conid_field(self, client: IBKRClient) -> None:
        """Test that missing conid field raises appropriate error."""
        # Mock response with dict but no conid field
        client.get_request = AsyncMock(return_value=[{"symbol": "AAPL", "description": "Apple Inc"}])

        with pytest.raises(KeyError):
            await client.get_conid("AAPL")

    @pytest.mark.asyncio
    async def test_get_conid_connection_error(self, client: IBKRClient) -> None:
        """Test that connection failures raise IBKRConnectionError."""
        # Mock connection error
        client.get_request = AsyncMock(side_effect=IBKRConnectionError())

        with pytest.raises(IBKRConnectionError):
            await client.get_conid("AAPL")

    @pytest.mark.asyncio
    async def test_get_conid_api_error_500(self, client: IBKRClient) -> None:
        """Test that server errors raise IBKRAPIError."""
        # Mock API error after retries exhausted
        client.get_request = AsyncMock(
            side_effect=IBKRAPIError(
                "Error with status code 500: Internal Server Error"
            )
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_conid("AAPL")

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_conid_api_error_400(self, client: IBKRClient) -> None:
        """Test that client errors raise IBKRAPIError."""
        # Mock API error for bad request
        client.get_request = AsyncMock(
            side_effect=IBKRAPIError("Error with status code 400: Bad Request")
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_conid("AAPL")

        assert "400" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_conid_picks_first_result(self, client: IBKRClient) -> None:
        """Test that multiple results returns the first (SMART) conid."""
        # Mock multiple results (ambiguous)
        client.get_request = AsyncMock(
            return_value=[
                {"conid": 265598, "symbol": "AAPL", "exchange": "SMART"},
                {"conid": 123456, "symbol": "AAPL", "exchange": "NASDAQ"},
            ]
        )

        conid = await client.get_conid("AAPL")

        # Should return first result
        assert conid == 265598

    @pytest.mark.asyncio
    async def test_get_conid_default_asset_type_stk(self, client: IBKRClient) -> None:
        """Test that default asset type is STK."""
        client.get_request = AsyncMock(
            return_value=[{"conid": 265598, "symbol": "AAPL"}]
        )

        await client.get_conid("AAPL")

        # Verify the params included default asset_type=STK
        client.get_request.assert_called_once()
        call_kwargs = client.get_request.call_args[1]
        assert "params" in call_kwargs
        assert call_kwargs["params"]["symbol"] == "AAPL"
        assert call_kwargs["params"]["assetType"] == "STK"

    @pytest.mark.asyncio
    async def test_get_conid_custom_asset_type(self, client: IBKRClient) -> None:
        """Test that custom asset_type parameter is passed correctly."""
        client.get_request = AsyncMock(
            return_value=[{"conid": 999999, "symbol": "ES", "assetType": "FUT"}]
        )

        # Request futures contract
        conid = await client.get_conid("ES", asset_type="FUT")

        assert conid == 999999
        # Verify the params included custom asset_type
        client.get_request.assert_called_once()
        call_kwargs = client.get_request.call_args[1]
        assert "params" in call_kwargs
        assert call_kwargs["params"]["symbol"] == "ES"
        assert call_kwargs["params"]["assetType"] == "FUT"

    @pytest.mark.asyncio
    async def test_get_conid_different_symbols_different_cache_entries(
        self, client: IBKRClient
    ) -> None:
        """Test that different symbols create separate cache entries."""
        client.get_request = AsyncMock(
            side_effect=[
                [{"conid": 265598, "symbol": "AAPL"}],
                [{"conid": 456789, "symbol": "MSFT"}],
            ]
        )

        conid1 = await client.get_conid("AAPL")
        conid2 = await client.get_conid("MSFT")

        # Should call API twice (different symbols)
        assert client.get_request.call_count == 2
        assert conid1 == 265598
        assert conid2 == 456789

    @pytest.mark.asyncio
    async def test_get_conid_integer_return_type(self, client: IBKRClient) -> None:
        """Test that conid is returned as integer even if API returns string."""
        # Mock response with string conid
        client.get_request = AsyncMock(
            return_value=[{"conid": "265598", "symbol": "AAPL"}]
        )

        conid = await client.get_conid("AAPL")

        assert conid == 265598
        assert isinstance(conid, int)


class TestGetSearchResults:
    """Test get_search_results helper method."""

    @pytest.mark.asyncio
    async def test_get_search_results_success(self, client: IBKRClient) -> None:
        """Test successful search results."""
        mock_response = [
            {"conid": 265598, "symbol": "AAPL", "description": "Apple Inc"}
        ]
        client.get_request = AsyncMock(return_value=mock_response)

        results = await client.get_search_results("AAPL", "STK", timedelta(hours=24))

        assert results == mock_response
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_get_search_results_caching(self, client: IBKRClient) -> None:
        """Test that search results are cached."""
        mock_response = [{"conid": 265598, "symbol": "AAPL"}]
        client.get_request = AsyncMock(return_value=mock_response)

        # First call
        results1 = await client.get_search_results("AAPL", "STK", timedelta(hours=24))

        # Second call - should use cache
        results2 = await client.get_search_results("AAPL", "STK", timedelta(hours=24))

        # Should only call API once
        assert client.get_request.call_count == 1
        assert results1 == results2

    @pytest.mark.asyncio
    async def test_get_search_results_no_ttl(self, client: IBKRClient) -> None:
        """Test search results with no TTL."""
        mock_response = [{"conid": 265598, "symbol": "AAPL"}]
        client.get_request = AsyncMock(return_value=mock_response)

        results = await client.get_search_results("AAPL", "STK", None)

        assert results == mock_response

    @pytest.mark.asyncio
    async def test_get_search_results_invalid_response(self, client: IBKRClient) -> None:
        """Test that invalid search results raise SymbolNotFoundError."""
        client.get_request = AsyncMock(return_value={"error": "not a list"})

        with pytest.raises(SymbolNotFoundError):
            await client.get_search_results("INVALID", "STK", timedelta(hours=24))

    @pytest.mark.asyncio
    async def test_get_search_results_special_char_dot(self, client: IBKRClient) -> None:
        """Test that symbols with dots (e.g., BRK.B) are properly URL encoded."""
        mock_response = [{"conid": 4815747, "symbol": "BRK.B", "description": "Berkshire Hathaway Inc"}]
        client.get_request = AsyncMock(return_value=mock_response)

        results = await client.get_search_results("BRK.B", "STK", timedelta(hours=24))

        assert results == mock_response
        # Verify get_request was called with params
        client.get_request.assert_called_once()
        call_kwargs = client.get_request.call_args[1]
        assert "params" in call_kwargs
        assert call_kwargs["params"]["symbol"] == "BRK.B"
        assert call_kwargs["params"]["assetType"] == "STK"

    @pytest.mark.asyncio
    async def test_get_search_results_special_char_ampersand(self, client: IBKRClient) -> None:
        """Test that symbols with ampersands (e.g., AT&T) are properly URL encoded."""
        mock_response = [{"conid": 12345, "symbol": "T", "description": "AT&T Inc"}]
        client.get_request = AsyncMock(return_value=mock_response)

        results = await client.get_search_results("AT&T", "STK", timedelta(hours=24))

        assert results == mock_response
        # Verify params are passed correctly (httpx will URL-encode the ampersand)
        client.get_request.assert_called_once()
        call_kwargs = client.get_request.call_args[1]
        assert call_kwargs["params"]["symbol"] == "AT&T"

    @pytest.mark.asyncio
    async def test_get_search_results_special_char_space(self, client: IBKRClient) -> None:
        """Test that symbols with spaces are properly URL encoded."""
        mock_response = [{"conid": 99999, "symbol": "TEST SYMBOL"}]
        client.get_request = AsyncMock(return_value=mock_response)

        results = await client.get_search_results("TEST SYMBOL", "STK", timedelta(hours=24))

        assert results == mock_response
        client.get_request.assert_called_once()
        call_kwargs = client.get_request.call_args[1]
        assert call_kwargs["params"]["symbol"] == "TEST SYMBOL"

    @pytest.mark.asyncio
    async def test_get_search_results_special_char_slash(self, client: IBKRClient) -> None:
        """Test that symbols with slashes are properly URL encoded."""
        mock_response = [{"conid": 88888, "symbol": "BRK/B"}]
        client.get_request = AsyncMock(return_value=mock_response)

        results = await client.get_search_results("BRK/B", "STK", timedelta(hours=24))

        assert results == mock_response
        client.get_request.assert_called_once()
        call_kwargs = client.get_request.call_args[1]
        assert call_kwargs["params"]["symbol"] == "BRK/B"


class TestGetStock:
    """Test get_stock method that combines search and snapshot."""

    @pytest.mark.asyncio
    async def test_get_stock_success(self, client: IBKRClient) -> None:
        """Test successful stock quote retrieval."""
        # Mock search results with option expirations
        mock_search = [
            {
                "conid": 265598,
                "symbol": "AAPL",
                "sections": [
                    {"secType": "STK"},
                    {"secType": "OPT", "months": "DEC24;JAN25;FEB25"},
                ],
            }
        ]
        # Mock market snapshot
        mock_snapshot = [{"conid": 265598, "last": 150.25, "bid": 150.20, "ask": 150.30}]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        stock = await client.get_stock("AAPL")

        # Verify Stock object fields
        assert stock.symbol == "AAPL"
        assert stock.current_price == 150.25
        assert stock.conid == 265598
        assert stock.available_expirations == ["DEC24", "JAN25", "FEB25"]

    @pytest.mark.asyncio
    async def test_get_stock_uses_5min_cache_for_snapshot(
        self, client: IBKRClient
    ) -> None:
        """Test that market snapshot uses 5-minute cache TTL."""
        mock_search = [
            {
                "conid": 265598,
                "symbol": "AAPL",
                "sections": [{"secType": "OPT", "months": "DEC24"}],
            }
        ]
        mock_snapshot = [{"conid": 265598, "last": 150.0, "bid": 149.95, "ask": 150.05}]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        await client.get_stock("AAPL")

        # Verify get_market_snapshot was called with 5min TTL and stock fields
        client.get_market_snapshot.assert_called_once_with(
            265598, timedelta(minutes=5), fields=IBKRClient._STOCK_FIELDS
        )

    @pytest.mark.asyncio
    async def test_get_stock_no_option_sections(self, client: IBKRClient) -> None:
        """Test stock with no option sections returns empty expirations."""
        mock_search = [
            {
                "conid": 265598,
                "symbol": "AAPL",
                "sections": [{"secType": "STK"}],  # No OPT section
            }
        ]
        mock_snapshot = [{"conid": 265598, "last": 150.0, "bid": 149.95, "ask": 150.05}]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        stock = await client.get_stock("AAPL")

        assert stock.available_expirations == []

    @pytest.mark.asyncio
    async def test_get_stock_empty_sections(self, client: IBKRClient) -> None:
        """Test stock with empty sections array."""
        mock_search = [
            {
                "conid": 265598,
                "symbol": "AAPL",
                "sections": [],  # Empty sections
            }
        ]
        mock_snapshot = [{"conid": 265598, "last": 150.0, "bid": 149.95, "ask": 150.05}]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        stock = await client.get_stock("AAPL")

        assert stock.available_expirations == []

    @pytest.mark.asyncio
    async def test_get_stock_multiple_opt_sections(self, client: IBKRClient) -> None:
        """Test that the last OPT section's months are used (loop overwrites)."""
        mock_search = [
            {
                "conid": 265598,
                "symbol": "AAPL",
                "sections": [
                    {"secType": "OPT", "months": "DEC24;JAN25"},
                    {"secType": "OPT", "months": "FEB25;MAR25"},  # Last one wins
                ],
            }
        ]
        mock_snapshot = [{"conid": 265598, "last": 150.0, "bid": 149.95, "ask": 150.05}]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        stock = await client.get_stock("AAPL")

        # Last OPT section's months (implementation overwrites in loop)
        assert stock.available_expirations == ["FEB25", "MAR25"]

    @pytest.mark.asyncio
    async def test_get_stock_conid_as_string_in_response(
        self, client: IBKRClient
    ) -> None:
        """Test that conid is properly converted even if returned as string."""
        mock_search = [
            {
                "conid": "265598",  # String instead of int
                "symbol": "AAPL",
                "sections": [],
            }
        ]
        mock_snapshot = [{"conid": 265598, "last": 150.0, "bid": 149.95, "ask": 150.05}]

        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        stock = await client.get_stock("AAPL")

        assert stock.conid == 265598
        assert isinstance(stock.conid, int)

    @pytest.mark.asyncio
    async def test_get_stock_symbol_not_found(self, client: IBKRClient) -> None:
        """Test that symbol not found errors propagate."""
        client.get_search_results = AsyncMock(
            side_effect=SymbolNotFoundError("INVALID")
        )

        with pytest.raises(SymbolNotFoundError) as exc_info:
            await client.get_stock("INVALID")

        assert "INVALID" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_stock_api_error(self, client: IBKRClient) -> None:
        """Test that API errors from search propagate."""
        client.get_search_results = AsyncMock(
            side_effect=IBKRAPIError("Error with status code 500")
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_stock("AAPL")

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_stock_snapshot_error(self, client: IBKRClient) -> None:
        """Test that API errors from snapshot propagate."""
        mock_search = [
            {
                "conid": 265598,
                "symbol": "AAPL",
                "sections": [],
            }
        ]
        client.get_search_results = AsyncMock(return_value=mock_search)
        client.get_market_snapshot = AsyncMock(
            side_effect=IBKRAPIError("Snapshot error")
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_stock("AAPL")

        assert "Snapshot error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_stock_connection_error(self, client: IBKRClient) -> None:
        """Test that connection errors propagate."""
        client.get_search_results = AsyncMock(side_effect=IBKRConnectionError())

        with pytest.raises(IBKRConnectionError):
            await client.get_stock("AAPL")


class TestGetOptionStrikes:
    """Test get_option_strikes method."""

    @pytest.mark.asyncio
    async def test_get_option_strikes_success(self, client: IBKRClient) -> None:
        """Test successful retrieval of option strikes."""
        mock_response = {
            "call": [100.0, 105.0, 110.0, 115.0, 120.0],
            "put": [100.0, 105.0, 110.0, 115.0, 120.0],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        strikes = await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

        assert strikes == mock_response
        assert "call" in strikes
        assert "put" in strikes
        assert len(strikes["call"]) == 5
        assert len(strikes["put"]) == 5

    @pytest.mark.asyncio
    async def test_get_option_strikes_uses_cache(self, client: IBKRClient) -> None:
        """Test that strikes are cached."""
        mock_response = {"call": [100.0, 110.0], "put": [100.0, 110.0]}
        client.get_request = AsyncMock(return_value=mock_response)

        # First call
        strikes1 = await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

        # Second call - should use cache
        strikes2 = await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

        # Should only call API once
        assert client.get_request.call_count == 1
        assert strikes1 == strikes2

    @pytest.mark.asyncio
    async def test_get_option_strikes_cache_ttl_15min(self, client: IBKRClient) -> None:
        """Test that strikes use 15-minute cache TTL."""
        with patch.object(client._cache, "set") as mock_cache_set:
            mock_response = {"call": [100.0], "put": [100.0]}
            client.get_request = AsyncMock(return_value=mock_response)

            await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

            # Verify cache.set was called with 15min TTL
            assert mock_cache_set.called
            call_args = mock_cache_set.call_args
            assert call_args[0][2] == timedelta(minutes=15)

    @pytest.mark.asyncio
    async def test_get_option_strikes_invalid_response(self, client: IBKRClient) -> None:
        """Test that invalid response raises IBKRAPIError."""
        # Mock response that's not a dict with 2 keys
        client.get_request = AsyncMock(return_value={"call": [100.0]})

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

        assert "Invalid option strikes" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_option_strikes_wrong_type(self, client: IBKRClient) -> None:
        """Test that non-dict response raises IBKRAPIError."""
        client.get_request = AsyncMock(return_value=[100.0, 110.0])

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))

        assert "Invalid option strikes" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_option_strikes_different_months(self, client: IBKRClient) -> None:
        """Test that different months create separate cache entries."""
        client.get_request = AsyncMock(
            side_effect=[
                {"call": [100.0], "put": [100.0]},
                {"call": [110.0], "put": [110.0]},
            ]
        )

        strikes1 = await client.get_option_strikes(265598, "JAN25", timedelta(minutes=15))
        strikes2 = await client.get_option_strikes(265598, "FEB25", timedelta(minutes=15))

        # Should call API twice (different months)
        assert client.get_request.call_count == 2
        assert strikes1["call"] == [100.0]
        assert strikes2["call"] == [110.0]


class TestGetUnpricedOptionContract:
    """Test get_unpriced_option_contract method."""

    @pytest.mark.asyncio
    async def test_get_unpriced_option_contract_call(self, client: IBKRClient) -> None:
        """Test successful retrieval of call option contract."""
        mock_response = [
            {
                "conid": 12345,
                "symbol": "AAPL",
                "maturityDate": "20250117",
                "strike": 150.0,
                "right": "C",
            }
        ]
        client.get_request = AsyncMock(return_value=mock_response)

        contract = await client.get_unpriced_option_contract(
            265598, "JAN25", "C", 150.0, timedelta(minutes=15)
        )

        assert contract.conid == 12345
        assert contract.strike == 150.0
        assert contract.right == "C"
        assert contract.expiration.year == 2025
        assert contract.expiration.month == 1
        assert contract.expiration.day == 17
        # Price fields should be None (unpriced)
        assert contract.bid is None
        assert contract.ask is None

    @pytest.mark.asyncio
    async def test_get_unpriced_option_contract_put(self, client: IBKRClient) -> None:
        """Test successful retrieval of put option contract."""
        mock_response = [
            {
                "conid": 54321,
                "symbol": "AAPL",
                "maturityDate": "20250117",
                "strike": 150.0,
                "right": "P",
            }
        ]
        client.get_request = AsyncMock(return_value=mock_response)

        contract = await client.get_unpriced_option_contract(
            265598, "JAN25", "P", 150.0, timedelta(minutes=15)
        )

        assert contract.conid == 54321
        assert contract.strike == 150.0
        assert contract.right == "P"

    @pytest.mark.asyncio
    async def test_get_unpriced_option_contract_uses_cache(
        self, client: IBKRClient
    ) -> None:
        """Test that contracts are cached."""
        mock_response = [{"conid": 12345, "maturityDate": "20250117"}]
        client.get_request = AsyncMock(return_value=mock_response)

        # First call
        contract1 = await client.get_unpriced_option_contract(
            265598, "JAN25", "C", 150.0, timedelta(minutes=15)
        )

        # Second call - should use cache
        contract2 = await client.get_unpriced_option_contract(
            265598, "JAN25", "C", 150.0, timedelta(minutes=15)
        )

        # Should only call API once
        assert client.get_request.call_count == 1
        assert contract1.conid == contract2.conid

    @pytest.mark.asyncio
    async def test_get_unpriced_option_contract_invalid_response(
        self, client: IBKRClient
    ) -> None:
        """Test that invalid response raises IBKRAPIError."""
        client.get_request = AsyncMock(return_value={"error": "not a list"})

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_unpriced_option_contract(
                265598, "JAN25", "C", 150.0, timedelta(minutes=15)
            )

        assert "Invalid contract info response" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_unpriced_option_contract_conid_as_string(
        self, client: IBKRClient
    ) -> None:
        """Test that string conid is converted to int."""
        mock_response = [{"conid": "12345", "maturityDate": "20250117"}]
        client.get_request = AsyncMock(return_value=mock_response)

        contract = await client.get_unpriced_option_contract(
            265598, "JAN25", "C", 150.0, timedelta(minutes=15)
        )

        assert contract.conid == 12345
        assert isinstance(contract.conid, int)


class TestGetUnpricedOptionChain:
    """Test get_unpriced_option_chain method."""

    @pytest.mark.asyncio
    async def test_get_unpriced_option_chain_success(self, client: IBKRClient) -> None:
        """Test successful retrieval of unpriced option chain."""
        # Mock strikes
        mock_strikes = {"call": [145.0, 150.0, 155.0], "put": [145.0, 150.0, 155.0]}

        # Mock call contracts
        mock_call_145 = [{"conid": 1001, "maturityDate": "20250117"}]
        mock_call_150 = [{"conid": 1002, "maturityDate": "20250117"}]
        mock_call_155 = [{"conid": 1003, "maturityDate": "20250117"}]

        # Mock put contracts
        mock_put_145 = [{"conid": 2001, "maturityDate": "20250117"}]
        mock_put_150 = [{"conid": 2002, "maturityDate": "20250117"}]
        mock_put_155 = [{"conid": 2003, "maturityDate": "20250117"}]

        # Set up mock to return different responses
        client.get_request = AsyncMock(
            side_effect=[
                mock_strikes,
                mock_call_145,
                mock_call_150,
                mock_call_155,
                mock_put_145,
                mock_put_150,
                mock_put_155,
            ]
        )

        chain = await client.get_unpriced_option_chain(265598, "JAN25")

        # Verify chain structure
        assert len(chain.calls) == 3
        assert len(chain.puts) == 3
        assert chain.expiration.year == 2025
        assert chain.expiration.month == 1
        assert chain.expiration.day == 17

        # Verify call strikes
        call_strikes = [c.strike for c in chain.calls]
        assert 145.0 in call_strikes
        assert 150.0 in call_strikes
        assert 155.0 in call_strikes

        # Verify put strikes
        put_strikes = [p.strike for p in chain.puts]
        assert 145.0 in put_strikes
        assert 150.0 in put_strikes
        assert 155.0 in put_strikes

    @pytest.mark.asyncio
    async def test_get_unpriced_option_chain_no_calls(self, client: IBKRClient) -> None:
        """Test chain with only puts (no calls)."""
        mock_strikes = {"call": [], "put": [150.0]}
        mock_put_150 = [{"conid": 2002, "maturityDate": "20250117"}]

        client.get_request = AsyncMock(side_effect=[mock_strikes, mock_put_150])

        chain = await client.get_unpriced_option_chain(265598, "JAN25")

        assert len(chain.calls) == 0
        assert len(chain.puts) == 1
        # Should use put expiration
        assert chain.expiration.year == 2025

    @pytest.mark.asyncio
    async def test_get_unpriced_option_chain_no_puts(self, client: IBKRClient) -> None:
        """Test chain with only calls (no puts)."""
        mock_strikes = {"call": [150.0], "put": []}
        mock_call_150 = [{"conid": 1002, "maturityDate": "20250117"}]

        client.get_request = AsyncMock(side_effect=[mock_strikes, mock_call_150])

        chain = await client.get_unpriced_option_chain(265598, "JAN25")

        assert len(chain.calls) == 1
        assert len(chain.puts) == 0
        # Should use call expiration
        assert chain.expiration.year == 2025

    @pytest.mark.asyncio
    async def test_get_unpriced_option_chain_empty(self, client: IBKRClient) -> None:
        """Test that chain with no calls or puts raises error."""
        mock_strikes = {"call": [], "put": []}
        client.get_request = AsyncMock(return_value=mock_strikes)

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_unpriced_option_chain(265598, "JAN25")

        assert "neither puts or calls" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_unpriced_option_chain_uses_15min_cache(
        self, client: IBKRClient
    ) -> None:
        """Test that chain components use 15-minute cache."""
        mock_strikes = {"call": [150.0], "put": []}
        mock_call_150 = [{"conid": 1002, "maturityDate": "20250117"}]

        client.get_request = AsyncMock(side_effect=[mock_strikes, mock_call_150])

        await client.get_unpriced_option_chain(265598, "JAN25")

        # Verify get_request was called
        assert client.get_request.call_count == 2


class TestPriceOptionChain:
    """Test price_option_chain method."""

    @pytest.mark.asyncio
    async def test_price_option_chain_success(self, client: IBKRClient) -> None:
        """Test successful pricing of option chain."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        # Create unpriced chain
        call1 = OptionContract(
            conid=1001, strike=145.0, right="C", expiration=date(2025, 1, 17)
        )
        call2 = OptionContract(
            conid=1002, strike=150.0, right="C", expiration=date(2025, 1, 17)
        )
        put1 = OptionContract(
            conid=2001, strike=145.0, right="P", expiration=date(2025, 1, 17)
        )
        put2 = OptionContract(
            conid=2002, strike=150.0, right="P", expiration=date(2025, 1, 17)
        )

        chain = OptionChain(
            expiration=date(2025, 1, 17),
            calls=[call1, call2],
            puts=[put1, put2],
        )

        # Mock market snapshot
        mock_snapshot = [
            {"conid": 1001, "bid": 5.0, "ask": 5.2},
            {"conid": 1002, "bid": 3.0, "ask": 3.2},
            {"conid": 2001, "bid": 4.5, "ask": 4.7},
            {"conid": 2002, "bid": 2.5, "ask": 2.7},
        ]
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        await client.price_option_chain(chain)

        # Verify all contracts are priced
        assert call1.bid == 5.0
        assert call1.ask == 5.2
        assert call2.bid == 3.0
        assert call2.ask == 3.2
        assert put1.bid == 4.5
        assert put1.ask == 4.7
        assert put2.bid == 2.5
        assert put2.ask == 2.7

    @pytest.mark.asyncio
    async def test_price_option_chain_batching(self, client: IBKRClient) -> None:
        """Test that pricing batches requests for rate limiting."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        # Create chain with 20 contracts (should trigger 3 batches with batch size 9)
        calls = [
            OptionContract(
                conid=1000 + i, strike=100.0 + i, right="C", expiration=date(2025, 1, 17)
            )
            for i in range(10)
        ]
        puts = [
            OptionContract(
                conid=2000 + i, strike=100.0 + i, right="P", expiration=date(2025, 1, 17)
            )
            for i in range(10)
        ]

        chain = OptionChain(expiration=date(2025, 1, 17), calls=calls, puts=puts)

        # Mock snapshot returns - should be called 3 times (9 + 9 + 2)
        mock_snapshots = [
            # Batch 1: 9 contracts
            [{"conid": 1000 + i, "bid": 1.0, "ask": 1.1} for i in range(9)],
            # Batch 2: 9 contracts
            [{"conid": 1009, "bid": 1.0, "ask": 1.1}]
            + [{"conid": 2000 + i, "bid": 2.0, "ask": 2.1} for i in range(8)],
            # Batch 3: 2 contracts
            [{"conid": 2008 + i, "bid": 2.0, "ask": 2.1} for i in range(2)],
        ]
        client.get_market_snapshot = AsyncMock(side_effect=mock_snapshots)

        await client.price_option_chain(chain)

        # Should have made 3 batched requests
        assert client.get_market_snapshot.call_count == 3

        # Verify first and last contracts are priced
        assert calls[0].bid == 1.0
        assert puts[-1].ask == 2.1

    @pytest.mark.asyncio
    async def test_price_option_chain_snapshot_mismatch(
        self, client: IBKRClient
    ) -> None:
        """Test error when snapshot conid doesn't match any contract."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        call1 = OptionContract(
            conid=1001, strike=145.0, right="C", expiration=date(2025, 1, 17)
        )
        chain = OptionChain(expiration=date(2025, 1, 17), calls=[call1], puts=[])

        # Mock snapshot with wrong conid
        mock_snapshot = [{"conid": 9999, "bid": 5.0, "ask": 5.2}]
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.price_option_chain(chain)

        assert "does not correspond to contract" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_price_option_chain_invalid_conid_in_snapshot(
        self, client: IBKRClient
    ) -> None:
        """Test error when snapshot has invalid conid type."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        call1 = OptionContract(
            conid=1001, strike=145.0, right="C", expiration=date(2025, 1, 17)
        )
        chain = OptionChain(expiration=date(2025, 1, 17), calls=[call1], puts=[])

        # Mock snapshot with string conid
        mock_snapshot = [{"conid": "not_an_int", "bid": 5.0, "ask": 5.2}]
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.price_option_chain(chain)

        assert "Invalid conid in snapshot" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_price_option_chain_uses_15min_cache(
        self, client: IBKRClient
    ) -> None:
        """Test that pricing uses 15-minute cache TTL."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        call1 = OptionContract(
            conid=1001, strike=145.0, right="C", expiration=date(2025, 1, 17)
        )
        chain = OptionChain(expiration=date(2025, 1, 17), calls=[call1], puts=[])

        mock_snapshot = [{"conid": 1001, "bid": 5.0, "ask": 5.2}]
        client.get_market_snapshot = AsyncMock(return_value=mock_snapshot)

        await client.price_option_chain(chain)

        # Verify get_market_snapshot was called with 15min TTL
        client.get_market_snapshot.assert_called()
        call_args = client.get_market_snapshot.call_args
        assert call_args[0][1] == timedelta(minutes=15)


class TestGetOptionChain:
    """Test get_option_chain main method."""

    @pytest.mark.asyncio
    async def test_get_option_chain_success(self, client: IBKRClient) -> None:
        """Test successful retrieval of fully priced option chain."""
        from datetime import date

        from option_analyzer.models.domain import OptionContract

        # Mock the unpriced chain
        mock_unpriced_call = OptionContract(
            conid=1001, strike=150.0, right="C", expiration=date(2025, 1, 17)
        )
        mock_unpriced_put = OptionContract(
            conid=2001, strike=150.0, right="P", expiration=date(2025, 1, 17)
        )

        from option_analyzer.models.domain import OptionChain

        mock_unpriced_chain = OptionChain(
            expiration=date(2025, 1, 17),
            calls=[mock_unpriced_call],
            puts=[mock_unpriced_put],
        )

        # Mock the pricing
        client.get_unpriced_option_chain = AsyncMock(return_value=mock_unpriced_chain)
        client.price_option_chain = AsyncMock()

        chain = await client.get_option_chain(265598, "JAN25")

        # Verify both methods were called
        client.get_unpriced_option_chain.assert_called_once_with(265598, "JAN25")
        client.price_option_chain.assert_called_once_with(mock_unpriced_chain)

        # Verify chain is returned
        assert chain == mock_unpriced_chain
        assert len(chain.calls) == 1
        assert len(chain.puts) == 1

    @pytest.mark.asyncio
    async def test_get_option_chain_propagates_errors(
        self, client: IBKRClient
    ) -> None:
        """Test that errors from sub-methods propagate."""
        client.get_unpriced_option_chain = AsyncMock(
            side_effect=IBKRAPIError("Failed to get strikes")
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_option_chain(265598, "JAN25")

        assert "Failed to get strikes" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_option_chain_pricing_error_propagates(
        self, client: IBKRClient
    ) -> None:
        """Test that pricing errors propagate."""
        from datetime import date

        from option_analyzer.models.domain import OptionChain, OptionContract

        mock_unpriced_chain = OptionChain(
            expiration=date(2025, 1, 17),
            calls=[
                OptionContract(
                    conid=1001, strike=150.0, right="C", expiration=date(2025, 1, 17)
                )
            ],
            puts=[],
        )

        client.get_unpriced_option_chain = AsyncMock(return_value=mock_unpriced_chain)
        client.price_option_chain = AsyncMock(
            side_effect=IBKRAPIError("Failed to get snapshot")
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_option_chain(265598, "JAN25")

        assert "Failed to get snapshot" in str(exc_info.value)


class TestGetHistoricalData:
    """Test get_historical_data method."""

    @pytest.mark.asyncio
    async def test_get_historical_data_success_1_year(self, client: IBKRClient) -> None:
        """Test successful retrieval of 1 year historical data."""
        mock_response = {
            "symbol": "AAPL",
            "data": [
                {"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000},
                {"t": 1672617600000, "o": 130.5, "h": 132.0, "l": 130.0, "c": 131.5, "v": 1100000},
                {"t": 1672704000000, "o": 131.5, "h": 133.0, "l": 131.0, "c": 132.0, "v": 1200000},
            ],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        result = await client.get_historical_data(265598, years=1)

        # Verify return format
        assert result["symbol"] == "AAPL"
        assert "closes" in result
        assert len(result["closes"]) == 3

        # Verify price entries format
        assert result["closes"][0]["date"] == "2023-01-01"
        assert result["closes"][0]["close"] == 130.5
        assert result["closes"][1]["date"] == "2023-01-02"
        assert result["closes"][1]["close"] == 131.5
        assert result["closes"][2]["date"] == "2023-01-03"
        assert result["closes"][2]["close"] == 132.0

    @pytest.mark.asyncio
    async def test_get_historical_data_success_3_years(self, client: IBKRClient) -> None:
        """Test successful retrieval of 3 years historical data (max)."""
        mock_response = {
            "symbol": "MSFT",
            "data": [
                {"t": 1577836800000, "o": 157.0, "h": 158.0, "l": 156.5, "c": 157.7, "v": 500000},
                {"t": 1672531200000, "o": 235.0, "h": 237.0, "l": 234.5, "c": 236.5, "v": 600000},
            ],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        result = await client.get_historical_data(265598, years=3)

        assert result["symbol"] == "MSFT"
        assert len(result["closes"]) == 2
        # Verify endpoint was called with correct period
        client.get_request.assert_called_once()
        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args
        assert "bar=1d" in call_args

    @pytest.mark.asyncio
    async def test_get_historical_data_uses_cache(self, client: IBKRClient) -> None:
        """Test that historical data uses cache on second call."""
        mock_response = {
            "symbol": "AAPL",
            "data": [
                {"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000},
            ],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        # First call - hits API
        result1 = await client.get_historical_data(265598, years=2)

        # Second call - should use cache
        result2 = await client.get_historical_data(265598, years=2)

        # Should only call API once
        assert client.get_request.call_count == 1
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_get_historical_data_cache_ttl_24h(self, client: IBKRClient) -> None:
        """Test that cache uses 24-hour TTL."""
        with patch.object(client._cache, "set") as mock_cache_set:
            mock_response = {
                "symbol": "AAPL",
                "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
            }
            client.get_request = AsyncMock(return_value=mock_response)

            await client.get_historical_data(265598, years=1)

            # Verify cache.set was called with 24h TTL
            assert mock_cache_set.called
            call_args = mock_cache_set.call_args
            # Third argument should be the TTL
            assert call_args[0][2] == timedelta(hours=24)

    @pytest.mark.asyncio
    async def test_get_historical_data_default_years_3(self, client: IBKRClient) -> None:
        """Test that default years parameter is 3."""
        mock_response = {
            "symbol": "AAPL",
            "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        # Call without years parameter (should default to 3)
        result = await client.get_historical_data(265598)

        # Verify endpoint was called with period=3y
        client.get_request.assert_called_once()
        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args

    @pytest.mark.asyncio
    async def test_get_historical_data_invalid_years_negative(
        self, client: IBKRClient
    ) -> None:
        """Test that negative years are clamped to 3."""
        mock_response = {
            "symbol": "AAPL",
            "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        # Call with invalid negative years
        result = await client.get_historical_data(265598, years=-1)

        # Should clamp to 3 years
        client.get_request.assert_called_once()
        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args

    @pytest.mark.asyncio
    async def test_get_historical_data_invalid_years_zero(
        self, client: IBKRClient
    ) -> None:
        """Test that zero years are clamped to 3."""
        mock_response = {
            "symbol": "AAPL",
            "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        result = await client.get_historical_data(265598, years=0)

        # Should clamp to 3 years
        client.get_request.assert_called_once()
        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args

    @pytest.mark.asyncio
    async def test_get_historical_data_invalid_years_too_large(
        self, client: IBKRClient
    ) -> None:
        """Test that years > 3 are clamped to 3."""
        mock_response = {
            "symbol": "AAPL",
            "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        result = await client.get_historical_data(265598, years=5)

        # Should clamp to 3 years
        client.get_request.assert_called_once()
        call_args = client.get_request.call_args[0][0]
        assert "period=3y" in call_args

    @pytest.mark.asyncio
    async def test_get_historical_data_missing_data_field(
        self, client: IBKRClient
    ) -> None:
        """Test that missing 'data' field raises IBKRAPIError."""
        # Mock response without 'data' field
        mock_response = {"symbol": "AAPL", "error": "no data"}
        client.get_request = AsyncMock(return_value=mock_response)

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_historical_data(265598, years=1)

        assert "Historical data missing from response" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_historical_data_connection_error(
        self, client: IBKRClient
    ) -> None:
        """Test that connection failures raise IBKRConnectionError."""
        client.get_request = AsyncMock(side_effect=IBKRConnectionError())

        with pytest.raises(IBKRConnectionError):
            await client.get_historical_data(265598, years=1)

    @pytest.mark.asyncio
    async def test_get_historical_data_api_error_500(self, client: IBKRClient) -> None:
        """Test that server errors raise IBKRAPIError."""
        client.get_request = AsyncMock(
            side_effect=IBKRAPIError("Error with status code 500: Internal Server Error")
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_historical_data(265598, years=1)

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_historical_data_api_error_400(self, client: IBKRClient) -> None:
        """Test that client errors raise IBKRAPIError."""
        client.get_request = AsyncMock(
            side_effect=IBKRAPIError("Error with status code 400: Bad Request")
        )

        with pytest.raises(IBKRAPIError) as exc_info:
            await client.get_historical_data(265598, years=1)

        assert "400" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_historical_data_date_format_iso(self, client: IBKRClient) -> None:
        """Test that dates are formatted as ISO 8601 strings."""
        mock_response = {
            "symbol": "AAPL",
            "data": [
                {"t": 1704067200000, "o": 180.0, "h": 181.0, "l": 179.5, "c": 180.5, "v": 1000000},  # 2024-01-01
            ],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        result = await client.get_historical_data(265598, years=1)

        # Verify ISO 8601 date format (YYYY-MM-DD)
        assert result["closes"][0]["date"] == "2024-01-01"
        # Verify it's a string, not a date object
        assert isinstance(result["closes"][0]["date"], str)

    @pytest.mark.asyncio
    async def test_get_historical_data_empty_data_list(
        self, client: IBKRClient
    ) -> None:
        """Test that empty data list returns empty prices list."""
        mock_response = {"symbol": "AAPL", "data": []}
        client.get_request = AsyncMock(return_value=mock_response)

        result = await client.get_historical_data(265598, years=1)

        assert result["symbol"] == "AAPL"
        assert result["closes"] == []

    @pytest.mark.asyncio
    async def test_get_historical_data_different_conids_different_cache(
        self, client: IBKRClient
    ) -> None:
        """Test that different conids create separate cache entries."""
        mock_response1 = {
            "symbol": "AAPL",
            "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
        }
        mock_response2 = {
            "symbol": "MSFT",
            "data": [{"t": 1672531200000, "o": 235.0, "h": 237.0, "l": 234.5, "c": 236.5, "v": 600000}],
        }
        client.get_request = AsyncMock(side_effect=[mock_response1, mock_response2])

        result1 = await client.get_historical_data(265598, years=1)  # AAPL
        result2 = await client.get_historical_data(456789, years=1)  # MSFT

        # Should call API twice (different conids)
        assert client.get_request.call_count == 2
        assert result1["symbol"] == "AAPL"
        assert result2["symbol"] == "MSFT"

    @pytest.mark.asyncio
    async def test_get_historical_data_different_years_different_cache(
        self, client: IBKRClient
    ) -> None:
        """Test that different years create separate cache entries."""
        mock_response1 = {
            "symbol": "AAPL",
            "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
        }
        mock_response2 = {
            "symbol": "AAPL",
            "data": [
                {"t": 1640995200000, "o": 177.0, "h": 178.0, "l": 176.5, "c": 177.5, "v": 900000},
                {"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000},
            ],
        }
        client.get_request = AsyncMock(side_effect=[mock_response1, mock_response2])

        result1 = await client.get_historical_data(265598, years=1)
        result2 = await client.get_historical_data(265598, years=2)

        # Should call API twice (different years)
        assert client.get_request.call_count == 2
        assert len(result1["closes"]) == 1
        assert len(result2["closes"]) == 2

    @pytest.mark.asyncio
    async def test_get_historical_data_close_price_extraction(
        self, client: IBKRClient
    ) -> None:
        """Test that only close prices are extracted from OHLCV data."""
        mock_response = {
            "symbol": "AAPL",
            "data": [
                {"t": 1672531200000, "o": 130.0, "h": 135.0, "l": 128.0, "c": 132.5, "v": 1000000},
            ],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        result = await client.get_historical_data(265598, years=1)

        # Verify only close price is in result
        assert result["closes"][0]["close"] == 132.5
        # Verify no other OHLCV fields
        assert "open" not in result["closes"][0]
        assert "high" not in result["closes"][0]
        assert "low" not in result["closes"][0]
        assert "volume" not in result["closes"][0]

    @pytest.mark.asyncio
    async def test_get_historical_data_endpoint_format(self, client: IBKRClient) -> None:
        """Test that endpoint is correctly formatted."""
        mock_response = {
            "symbol": "AAPL",
            "data": [{"t": 1672531200000, "o": 130.0, "h": 131.0, "l": 129.5, "c": 130.5, "v": 1000000}],
        }
        client.get_request = AsyncMock(return_value=mock_response)

        await client.get_historical_data(265598, years=2)

        # Verify endpoint format
        client.get_request.assert_called_once()
        call_args = client.get_request.call_args[0][0]
        assert call_args == "iserver/marketdata/history?conid=265598&period=2y&bar=1d"
