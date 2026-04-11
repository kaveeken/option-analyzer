"""
Fake IBKR client for testing without gateway dependency.

Provides a concrete implementation that returns canned data from test fixtures,
eliminating the need for unittest.mock and making tests more readable.
"""

import types
from datetime import date, timedelta
from typing import Any


def _month_key(s: str) -> str:
    """Normalise expiration string to MMMYY for internal key storage/lookup.

    Accepts DDMMMYY (e.g. '16JAN26') or plain MMMYY (e.g. 'JAN26').
    """
    if len(s) == 7 and s[:2].isdigit():
        return s[2:]
    return s

from option_analyzer.models.domain import OptionChain, Stock
from option_analyzer.utils.exceptions import IBKRAPIError, SymbolNotFoundError

from tests.fixtures.ibkr_responses import (
    make_stock,
    make_option_chain,
    make_historical_data,
)


class FakeIBKRClient:
    """
    Fake IBKR client that returns canned test data.

    This is a test double that matches IBKRClient's interface but returns
    configurable fixture data instead of making real API calls.

    Features:
    - Returns sensible defaults using shared test fixtures
    - Supports custom responses per symbol/conid
    - Supports error injection for testing failure scenarios
    - Same async context manager interface as IBKRClient

    Example - Basic usage:
        async with FakeIBKRClient() as client:
            stock = await client.get_stock("AAPL")
            # Returns default AAPL stock from fixtures

    Example - Custom data:
        client = FakeIBKRClient()
        client.add_stock("TSLA", make_stock("TSLA", current_price=200.0))
        async with client:
            stock = await client.get_stock("TSLA")
            assert stock.current_price == 200.0

    Example - Error injection:
        client = FakeIBKRClient()
        client.set_error("INVALID", SymbolNotFoundError("INVALID"))
        async with client:
            await client.get_stock("INVALID")  # Raises SymbolNotFoundError

    Example - FastAPI dependency override:
        app.dependency_overrides[get_ibkr_client] = lambda: FakeIBKRClient()
    """

    def __init__(self):
        """Initialize FakeIBKRClient with empty data stores."""
        # Data stores (populated on-demand or via add_* methods)
        self._stocks: dict[str, Stock] = {}
        self._chains: dict[tuple[int, str], OptionChain] = {}
        self._historical: dict[int, dict[str, Any]] = {}

        # Error injection (symbol/conid -> exception to raise)
        self._errors: dict[str, Exception] = {}

        # Track if closed
        self._closed = False

    # ========================================================================
    # Configuration Methods (for test setup)
    # ========================================================================

    def add_stock(self, symbol: str, stock: Stock) -> None:
        """Add a stock response for a specific symbol."""
        self._stocks[symbol] = stock

    def add_chain(self, conid: int, month: str, chain: OptionChain) -> None:
        """Add an option chain response for a specific conid and month.

        Accepts either DDMMMYY (e.g. '16JAN26') or MMMYY (e.g. 'JAN26') format;
        stored internally as MMMYY for uniform lookup.
        """
        self._chains[(conid, _month_key(month))] = chain

    def add_historical(self, conid: int, data: dict[str, Any]) -> None:
        """Add historical data response for a specific conid."""
        self._historical[conid] = data

    def set_error(self, key: str, error: Exception) -> None:
        """
        Configure an error to be raised for a specific symbol or conid.

        Args:
            key: Symbol (for get_stock) or conid as string (for get_option_chain)
            error: Exception instance to raise

        Example:
            client.set_error("INVALID", SymbolNotFoundError("INVALID"))
            client.set_error("265598", IBKRAPIError("Connection failed"))
        """
        self._errors[key] = error

    # ========================================================================
    # Async Context Manager Protocol
    # ========================================================================

    async def __aenter__(self) -> "FakeIBKRClient":
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit async context manager."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close the client (no-op for fake client)."""
        self._closed = True

    # ========================================================================
    # Public API Methods (matching IBKRClient interface)
    # ========================================================================

    async def get_stock(self, symbol: str) -> Stock:
        """
        Get stock information for a symbol.

        Returns pre-configured data if available, otherwise generates default
        data using the make_stock() fixture factory.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")

        Returns:
            Stock instance with current price and available expirations

        Raises:
            SymbolNotFoundError: If error configured for this symbol
        """
        # Check for error injection
        if symbol in self._errors:
            raise self._errors[symbol]

        # Return pre-configured stock if available
        if symbol in self._stocks:
            return self._stocks[symbol]

        # Generate default stock using fixtures
        stock = make_stock(
            symbol=symbol,
            current_price=150.0,
            available_expirations=["15JAN26", "15FEB26", "15MAR26"],
        )

        # Cache it for consistency within the test
        self._stocks[symbol] = stock
        return stock

    async def get_option_chain(self, conid: int, month: str) -> OptionChain:
        """
        Get option chain for a given contract and month.

        Returns pre-configured data if available, otherwise generates default
        data using the make_option_chain() fixture factory.

        Args:
            conid: IBKR contract ID
            month: Expiration — accepts DDMMMYY (e.g. '16JAN26') or MMMYY (e.g. 'JAN26')

        Returns:
            OptionChain with calls and puts

        Raises:
            IBKRAPIError: If error configured for this conid
        """
        # Check for error injection
        conid_key = str(conid)
        if conid_key in self._errors:
            raise self._errors[conid_key]

        # Normalise to MMMYY for key lookup (strips day prefix if present)
        month_code = _month_key(month)

        # Return pre-configured chain if available
        key = (conid, month_code)
        if key in self._chains:
            return self._chains[key]

        # Generate default chain using fixtures
        # Parse month to estimate expiration date (simplified)
        expiration = self._parse_month_to_date(month)

        chain = make_option_chain(
            expiration=expiration,
            strikes=[140.0, 150.0, 160.0],
            base_price=150.0,
            call_conid_start=conid * 10,
            put_conid_start=conid * 10 + 5000,
        )

        # Cache it for consistency within the test
        self._chains[(conid, month_code)] = chain
        return chain

    async def get_historical_data(self, conid: int, years: int = 3) -> dict[str, Any]:
        """
        Get historical price data for a contract.

        Returns pre-configured data if available, otherwise generates default
        data using the make_historical_data() fixture factory.

        Args:
            conid: IBKR contract ID
            years: Number of years of data (1-3)

        Returns:
            Dict with format: {"symbol": str, "closes": [{"close": float}, ...]}

        Raises:
            IBKRAPIError: If error configured for this conid
        """
        # Check for error injection
        conid_key = str(conid)
        if conid_key in self._errors:
            raise self._errors[conid_key]

        # Return pre-configured data if available
        if conid in self._historical:
            return self._historical[conid]

        # Generate default data using fixtures
        days = 252 * years  # Trading days per year
        data = make_historical_data(
            days=days,
            seed=conid,  # Use conid as seed for reproducible data
        )

        # Cache it for consistency within the test
        self._historical[conid] = data
        return data

    # ========================================================================
    # Helper Methods
    # ========================================================================

    @staticmethod
    def _parse_month_to_date(month: str) -> date:
        """
        Parse an expiration string to a date.

        Accepts DDMMMYY (e.g. '16JAN26') or MMMYY (e.g. 'JAN26').
        For MMMYY, uses the 15th of the month as a simplified approximation.

        Args:
            month: Expiration string

        Returns:
            Expiration date
        """
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
            "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
            "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }

        try:
            # DDMMMYY: "16JAN26"
            if len(month) == 7 and month[:2].isdigit():
                day = int(month[:2])
                month_abbr = month[2:5].upper()
                year = 2000 + int(month[5:7])
                return date(year, month_map[month_abbr], day)

            # MMMYY: "JAN26"
            month_abbr = month[:3].upper()
            year_suffix = month[3:5]
            month_num = month_map.get(month_abbr, 1)
            year = 2000 + int(year_suffix)
            return date(year, month_num, 15)

        except (ValueError, KeyError, IndexError):
            return date.today() + timedelta(days=30)


# ============================================================================
# Convenience Functions for Common Test Scenarios
# ============================================================================

def make_fake_client_with_stock(symbol: str = "AAPL", **stock_kwargs) -> FakeIBKRClient:
    """
    Create a FakeIBKRClient pre-configured with a stock.

    Args:
        symbol: Stock symbol
        **stock_kwargs: Additional arguments passed to make_stock()

    Returns:
        FakeIBKRClient with the stock pre-configured

    Example:
        client = make_fake_client_with_stock("TSLA", current_price=200.0)
    """
    client = FakeIBKRClient()
    stock = make_stock(symbol=symbol, **stock_kwargs)
    client.add_stock(symbol, stock)
    return client


def make_fake_client_with_error(symbol: str, error: Exception) -> FakeIBKRClient:
    """
    Create a FakeIBKRClient pre-configured to raise an error.

    Args:
        symbol: Symbol that will trigger the error
        error: Exception to raise

    Returns:
        FakeIBKRClient configured to raise the error

    Example:
        client = make_fake_client_with_error("INVALID", SymbolNotFoundError("INVALID"))
    """
    client = FakeIBKRClient()
    client.set_error(symbol, error)
    return client
