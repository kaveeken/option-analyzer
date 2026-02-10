"""
E2E test configuration with Playwright fixtures and mock IBKR client.
"""

import asyncio
from datetime import date, timedelta
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import Page, async_playwright

from option_analyzer.clients.ibkr import IBKRClient
from option_analyzer.models.domain import OptionChain, OptionContract, Stock
from tests.fixtures.ibkr_responses import (
    make_stock,
    make_option_chain,
    make_historical_data,
)
from tests.fixtures.fake_ibkr import FakeIBKRClient


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def browser():
    """Create a browser instance for the test session."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.fixture
async def page(browser) -> AsyncGenerator[Page, None]:
    """Create a new page for each test."""
    context = await browser.new_context()
    page = await context.new_page()
    yield page
    await context.close()


@pytest.fixture
def base_url() -> str:
    """Base URL for the test server."""
    return "http://localhost:8000"


@pytest.fixture
def mock_ibkr_client() -> FakeIBKRClient:
    """
    Create a fake IBKR client with standard responses.

    Returns a FakeIBKRClient configured with common test data for:
    - get_stock()
    - get_option_chain()
    - get_historical_data()
    """
    client = FakeIBKRClient()

    # Use shared fixtures with comprehensive data for thorough e2e tests
    expiration_date = date.today() + timedelta(days=30)

    client.add_stock("AAPL", make_stock(
        symbol="AAPL",
        current_price=150.0,
    ))

    # Comprehensive chain with multiple strikes
    client.add_chain(265598, "JAN26", make_option_chain(
        expiration=expiration_date,
        strikes=[140.0, 150.0, 160.0],
        base_price=150.0,
        call_conid_start=100001,
        put_conid_start=200001,
    ))

    # Historical data for analysis (reproducible with seed=42)
    client.add_historical(265598, make_historical_data(seed=42))

    return client


@pytest.fixture
async def setup_strategy(page: Page, base_url: str):
    """
    Helper to initialize a strategy with a stock symbol.

    Usage:
        await setup_strategy(page, "AAPL")
    """
    async def _setup(symbol: str = "AAPL"):
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Enter symbol and submit
        await page.fill("#symbol-input", symbol)
        await page.click("#symbol-submit")

        # Wait for stock info to appear
        await page.wait_for_selector("#stock-info:not(.hidden)", timeout=10000)

        return page

    return _setup


@pytest.fixture
async def setup_strategy_with_positions(page: Page, setup_strategy):
    """
    Helper to set up a strategy with option positions.

    Usage:
        await setup_strategy_with_positions(page, "AAPL", "JAN26", positions=[(100002, 2)])
    """
    async def _setup(
        symbol: str = "AAPL",
        month: str = "JAN26",
        positions: list[tuple[int, int]] = None,
        stock_quantity: int = 0
    ):
        # Initialize strategy
        await setup_strategy(symbol)

        # Load option chain
        await page.select_option("#month-selector", month)
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)", timeout=10000)

        # Add positions if specified
        if positions:
            for conid, quantity in positions:
                # Find and click the Add button for this conid
                await page.click(f'button[data-conid="{conid}"]')

                # Handle the prompt
                page.on("dialog", lambda dialog: dialog.accept(str(quantity)))

                # Wait for position to appear in table
                await page.wait_for_timeout(500)

        # Set stock quantity if specified
        if stock_quantity != 0:
            await page.fill("#stock-quantity-input", str(stock_quantity))
            await page.click("#stock-quantity-update")
            await page.wait_for_timeout(500)

        return page

    return _setup


@pytest.fixture
def sample_analysis_response():
    """Sample analysis response for testing."""
    return {
        "price_distribution": [
            {"lower": 140.0, "upper": 145.0, "count": 500, "midpoint": 142.5},
            {"lower": 145.0, "upper": 150.0, "count": 2000, "midpoint": 147.5},
            {"lower": 150.0, "upper": 155.0, "count": 4500, "midpoint": 152.5},
            {"lower": 155.0, "upper": 160.0, "count": 2000, "midpoint": 157.5},
            {"lower": 160.0, "upper": 165.0, "count": 1000, "midpoint": 162.5},
        ],
        "expected_value": 125.50,
        "probability_of_profit": 0.68,
        "max_gain": 1000.0,
        "max_loss": -500.0,
        "plot_url": "/static/plots/test_strategy.png",
    }
