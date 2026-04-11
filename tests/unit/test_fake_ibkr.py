"""
Tests for FakeIBKRClient.

Verifies that the fake client behaves correctly and provides the expected
test double functionality.
"""

import pytest

from option_analyzer.utils.exceptions import IBKRAPIError, SymbolNotFoundError
from tests.fixtures.fake_ibkr import (
    FakeIBKRClient,
    make_fake_client_with_error,
    make_fake_client_with_stock,
)
from tests.fixtures.ibkr_responses import make_option_chain, make_stock


class TestFakeIBKRClientBasics:
    """Test basic FakeIBKRClient functionality."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Test that FakeIBKRClient works as async context manager."""
        async with FakeIBKRClient() as client:
            assert client is not None
            assert not client._closed

        # After exiting context, should be closed
        assert client._closed

    @pytest.mark.asyncio
    async def test_get_stock_default(self):
        """Test get_stock with default fixture data."""
        async with FakeIBKRClient() as client:
            stock = await client.get_stock("AAPL")

            assert stock.symbol == "AAPL"
            assert stock.current_price == 150.0
            assert stock.conid > 0
            assert len(stock.available_expirations) > 0

    @pytest.mark.asyncio
    async def test_get_stock_cached(self):
        """Test that get_stock returns same object on multiple calls."""
        async with FakeIBKRClient() as client:
            stock1 = await client.get_stock("AAPL")
            stock2 = await client.get_stock("AAPL")

            # Should return the same cached object
            assert stock1 is stock2

    @pytest.mark.asyncio
    async def test_get_option_chain_default(self):
        """Test get_option_chain with default fixture data."""
        async with FakeIBKRClient() as client:
            chain = await client.get_option_chain(265598, "JAN26")

            assert len(chain.calls) == 3
            assert len(chain.puts) == 3
            assert chain.expiration is not None

    @pytest.mark.asyncio
    async def test_get_historical_data_default(self):
        """Test get_historical_data with default fixture data."""
        async with FakeIBKRClient() as client:
            data = await client.get_historical_data(265598, years=3)

            assert "closes" in data
            assert len(data["closes"]) == 252 * 3  # 3 years of trading days
            assert all("close" in entry for entry in data["closes"])


class TestFakeIBKRClientCustomData:
    """Test FakeIBKRClient with custom data."""

    @pytest.mark.asyncio
    async def test_add_stock(self):
        """Test adding custom stock data."""
        client = FakeIBKRClient()
        custom_stock = make_stock("TSLA", current_price=200.0)
        client.add_stock("TSLA", custom_stock)

        async with client:
            stock = await client.get_stock("TSLA")
            assert stock.symbol == "TSLA"
            assert stock.current_price == 200.0

    @pytest.mark.asyncio
    async def test_add_chain(self):
        """Test adding custom option chain data."""
        client = FakeIBKRClient()
        custom_chain = make_option_chain(strikes=[150.0])
        client.add_chain(265598, "JAN26", custom_chain)

        async with client:
            chain = await client.get_option_chain(265598, "JAN26")
            assert len(chain.calls) == 1
            assert chain.calls[0].strike == 150.0

    @pytest.mark.asyncio
    async def test_add_historical(self):
        """Test adding custom historical data."""
        client = FakeIBKRClient()
        custom_data = {
            "symbol": "AAPL",
            "closes": [{"close": 100.0}, {"close": 101.0}, {"close": 102.0}]
        }
        client.add_historical(265598, custom_data)

        async with client:
            data = await client.get_historical_data(265598)
            assert len(data["closes"]) == 3
            assert data["closes"][0]["close"] == 100.0


class TestFakeIBKRClientErrorInjection:
    """Test FakeIBKRClient error injection."""

    @pytest.mark.asyncio
    async def test_set_error_get_stock(self):
        """Test error injection for get_stock."""
        client = FakeIBKRClient()
        client.set_error("INVALID", SymbolNotFoundError("INVALID"))

        async with client:
            with pytest.raises(SymbolNotFoundError):
                await client.get_stock("INVALID")

    @pytest.mark.asyncio
    async def test_set_error_get_option_chain(self):
        """Test error injection for get_option_chain."""
        client = FakeIBKRClient()
        client.set_error("265598", IBKRAPIError("Connection failed"))

        async with client:
            with pytest.raises(IBKRAPIError):
                await client.get_option_chain(265598, "JAN26")

    @pytest.mark.asyncio
    async def test_set_error_get_historical_data(self):
        """Test error injection for get_historical_data."""
        client = FakeIBKRClient()
        client.set_error("265598", IBKRAPIError("No data available"))

        async with client:
            with pytest.raises(IBKRAPIError):
                await client.get_historical_data(265598)


class TestFakeIBKRClientConvenienceFunctions:
    """Test convenience functions for common scenarios."""

    @pytest.mark.asyncio
    async def test_make_fake_client_with_stock(self):
        """Test make_fake_client_with_stock convenience function."""
        client = make_fake_client_with_stock("GME", current_price=420.69)

        async with client:
            stock = await client.get_stock("GME")
            assert stock.symbol == "GME"
            assert stock.current_price == 420.69

    @pytest.mark.asyncio
    async def test_make_fake_client_with_error(self):
        """Test make_fake_client_with_error convenience function."""
        error = SymbolNotFoundError("INVALID")
        client = make_fake_client_with_error("INVALID", error)

        async with client:
            with pytest.raises(SymbolNotFoundError):
                await client.get_stock("INVALID")


class TestFakeIBKRClientMonthParsing:
    """Test month string parsing logic."""

    @pytest.mark.asyncio
    async def test_parse_standard_months(self):
        """Test parsing of standard month strings."""
        client = FakeIBKRClient()

        async with client:
            # These should all succeed without error
            chain_jan = await client.get_option_chain(265598, "JAN26")
            chain_feb = await client.get_option_chain(265598, "FEB26")
            chain_dec = await client.get_option_chain(265598, "DEC26")

            # Verify different months produce different expirations
            assert chain_jan.expiration != chain_feb.expiration
            assert chain_feb.expiration != chain_dec.expiration

    @pytest.mark.asyncio
    async def test_parse_invalid_month(self):
        """Test parsing of invalid month string (should fallback gracefully)."""
        client = FakeIBKRClient()

        async with client:
            # Should not crash, just use fallback date
            chain = await client.get_option_chain(265598, "INVALID")
            assert chain is not None
            assert chain.expiration is not None
