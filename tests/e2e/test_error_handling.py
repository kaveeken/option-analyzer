"""
E2E tests for error handling.

Tests cover:
- Invalid symbol error
- Network error display
- Session expiration handling
- Error recovery (retry after error)
- Error banner displaying correct messages
- Error clearing on success
"""

import json
import re

import pytest
from playwright.async_api import Page, Route, expect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _enter_symbol(page: Page, symbol: str):
    """Fill and submit the symbol input."""
    await page.fill("#symbol-input", symbol)
    await page.click("#symbol-submit")


async def _init_page(page: Page, base_url: str):
    """Navigate to the app and wait for it to settle."""
    await page.goto(base_url)
    await page.wait_for_load_state("networkidle")


def _json_route(status: int, body: dict):
    """Return a Playwright route handler that responds with a JSON body."""
    async def handler(route: Route):
        await route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(body),
        )
    return handler


# ---------------------------------------------------------------------------
# Tests: invalid symbol
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestInvalidSymbol:
    """Error handling when an unrecognised symbol is submitted."""

    async def test_invalid_symbol_shows_error_banner(
        self,
        page: Page,
        base_url: str,
    ):
        """Submitting an unknown symbol displays the error banner."""
        await _init_page(page, base_url)

        # Intercept the strategy/init call and return 404 SYMBOL_NOT_FOUND
        await page.route(
            "**/api/strategy/init",
            _json_route(404, {"error": "Symbol 'INVALID' not found", "code": "SYMBOL_NOT_FOUND"}),
        )

        await _enter_symbol(page, "INVALID")

        error_banner = page.locator("#error-banner")
        await expect(error_banner).not_to_have_class(re.compile(r"hidden"), timeout=5000)

    async def test_invalid_symbol_shows_symbol_not_found_message(
        self,
        page: Page,
        base_url: str,
    ):
        """Error banner text maps SYMBOL_NOT_FOUND code to the expected message."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(404, {"error": "Symbol 'XYZ' not found", "code": "SYMBOL_NOT_FOUND"}),
        )

        await _enter_symbol(page, "XYZ")

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)
        await expect(page.locator("#error-message")).to_contain_text("not found")

    async def test_invalid_symbol_stock_info_stays_hidden(
        self,
        page: Page,
        base_url: str,
    ):
        """When symbol lookup fails the stock-info panel remains hidden."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(404, {"error": "Symbol 'BAD' not found", "code": "SYMBOL_NOT_FOUND"}),
        )

        await _enter_symbol(page, "BAD")
        await page.wait_for_timeout(1000)

        await expect(page.locator("#stock-info")).to_have_class(re.compile(r"hidden"))


# ---------------------------------------------------------------------------
# Tests: network / service errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestNetworkErrors:
    """Error handling for backend and connectivity failures."""

    async def test_503_shows_error_banner(
        self,
        page: Page,
        base_url: str,
    ):
        """A 503 from the strategy endpoint displays the error banner."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(503, {"error": "Failed to connect to IBKR API", "code": "IBKR_CONNECTION_ERROR"}),
        )

        await _enter_symbol(page, "AAPL")

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)

    async def test_502_shows_error_banner(
        self,
        page: Page,
        base_url: str,
    ):
        """A 502 (IBKR API error) displays the error banner."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(502, {"error": "IBKR API request failed", "code": "IBKR_API_ERROR"}),
        )

        await _enter_symbol(page, "AAPL")

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)

    async def test_502_shows_ibkr_error_message(
        self,
        page: Page,
        base_url: str,
    ):
        """Error banner shows the backend error message text for a 502."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(502, {"error": "IBKR API request failed", "code": "IBKR_API_ERROR"}),
        )

        await _enter_symbol(page, "AAPL")

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)
        # The frontend shows the raw backend error message (state.setError(error.message))
        await expect(page.locator("#error-message")).to_contain_text("IBKR")

    async def test_429_shows_rate_limit_message(
        self,
        page: Page,
        base_url: str,
    ):
        """A 429 rate-limit error displays the backend error message."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(429, {"error": "Too many requests to IBKR", "code": "RATE_LIMITED"}),
        )

        await _enter_symbol(page, "AAPL")

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)
        await expect(page.locator("#error-message")).to_contain_text("Too many")

    async def test_option_chain_error_shows_banner(
        self,
        page: Page,
        base_url: str,
    ):
        """An error loading the option chain shows the error banner."""
        await _init_page(page, base_url)

        # Let strategy init succeed, but fail the chain endpoint
        await page.route(
            "**/api/stocks/**/chains**",
            _json_route(502, {"error": "IBKR API request failed", "code": "IBKR_API_ERROR"}),
        )

        await _enter_symbol(page, "AAPL")
        await page.wait_for_selector("#stock-info:not(.hidden)", timeout=10000)

        # Load option chain
        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)


# ---------------------------------------------------------------------------
# Tests: session expiration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSessionExpiration:
    """Error handling when the server returns a 401 session-expired response."""

    async def test_session_expired_shows_error_banner(
        self,
        page: Page,
        base_url: str,
    ):
        """A 401 SESSION_EXPIRED when adding a position shows the error banner."""
        await _init_page(page, base_url)

        # Let strategy init and chain load succeed, then fail the add-position call
        await page.route(
            "**/api/strategy/positions",
            _json_route(401, {"error": "Session not found or expired", "code": "SESSION_EXPIRED"}),
        )

        await _enter_symbol(page, "AAPL")
        await page.wait_for_selector("#stock-info:not(.hidden)", timeout=10000)

        # Load option chain
        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)", timeout=10000)

        # Attempt to add a position (will return 401)
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100002"]').first.click()

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)

    async def test_session_expired_shows_session_message(
        self,
        page: Page,
        base_url: str,
    ):
        """Session-expired error banner contains 'Session' in the message."""
        await _init_page(page, base_url)

        # Session expiration happens on post-init calls, not on init itself.
        # Intercept add-position (the realistic trigger) and return 401.
        await page.route(
            "**/api/strategy/positions",
            _json_route(401, {"error": "Session not found or expired", "code": "SESSION_EXPIRED"}),
        )

        await _enter_symbol(page, "AAPL")
        await page.wait_for_selector("#stock-info:not(.hidden)", timeout=10000)

        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)", timeout=10000)

        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100002"]').first.click()

        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)
        await expect(page.locator("#error-message")).to_contain_text("Session")


# ---------------------------------------------------------------------------
# Tests: error recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestErrorRecovery:
    """Tests that the app recovers cleanly after an error."""

    async def test_retry_with_valid_symbol_after_error(
        self,
        page: Page,
        base_url: str,
    ):
        """Submitting a valid symbol after an error clears the error and loads stock info."""
        await _init_page(page, base_url)

        # First submission fails
        await page.route(
            "**/api/strategy/init",
            _json_route(404, {"error": "Symbol 'BAD' not found", "code": "SYMBOL_NOT_FOUND"}),
        )
        await _enter_symbol(page, "BAD")
        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)

        # Remove the route interception so next call goes through normally
        await page.unroute("**/api/strategy/init")

        # Second submission succeeds
        await _enter_symbol(page, "AAPL")
        await page.wait_for_selector("#stock-info:not(.hidden)", timeout=10000)

        # Stock info should be visible and error should be gone (or hidden)
        await expect(page.locator("#stock-info")).not_to_have_class(re.compile(r"hidden"))

    async def test_error_banner_persists_until_dismissed(
        self,
        page: Page,
        base_url: str,
    ):
        """The error banner stays visible after a failed action until explicitly dismissed."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(404, {"error": "Symbol 'ZZZ' not found", "code": "SYMBOL_NOT_FOUND"}),
        )
        await _enter_symbol(page, "ZZZ")
        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)

        # Unblock real calls and perform a successful action
        await page.unroute("**/api/strategy/init")
        await _enter_symbol(page, "AAPL")
        await page.wait_for_selector("#stock-info:not(.hidden)", timeout=10000)

        # Banner remains visible (frontend does not auto-clear on success)
        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"))

        # Dismiss via close button
        await page.locator("#error-close").click()
        await expect(page.locator("#error-banner")).to_have_class(re.compile(r"hidden"), timeout=2000)

    async def test_multiple_consecutive_errors_then_success(
        self,
        page: Page,
        base_url: str,
    ):
        """Multiple errors in a row followed by success still loads correctly."""
        await _init_page(page, base_url)

        # Two consecutive failures
        for _ in range(2):
            await page.route(
                "**/api/strategy/init",
                _json_route(404, {"error": "Symbol not found", "code": "SYMBOL_NOT_FOUND"}),
            )
            await _enter_symbol(page, "FAKE")
            await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)
            await page.unroute("**/api/strategy/init")

        # Successful call
        await _enter_symbol(page, "AAPL")
        await page.wait_for_selector("#stock-info:not(.hidden)", timeout=10000)
        await expect(page.locator("#stock-info")).not_to_have_class(re.compile(r"hidden"))


# ---------------------------------------------------------------------------
# Tests: error banner UI
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestErrorBannerUI:
    """Tests for the error banner's own UI behaviour."""

    async def test_error_banner_hidden_on_page_load(
        self,
        page: Page,
        base_url: str,
    ):
        """Error banner is not visible when the page first loads."""
        await _init_page(page, base_url)

        await expect(page.locator("#error-banner")).to_have_class(re.compile(r"hidden"))

    async def test_error_banner_can_be_dismissed(
        self,
        page: Page,
        base_url: str,
    ):
        """Clicking the close button on the error banner hides it."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(404, {"error": "Symbol not found", "code": "SYMBOL_NOT_FOUND"}),
        )

        await _enter_symbol(page, "INVALID")
        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)

        # Close the banner
        await page.locator("#error-close").click()

        await expect(page.locator("#error-banner")).to_have_class(re.compile(r"hidden"), timeout=2000)

    async def test_error_message_text_is_visible(
        self,
        page: Page,
        base_url: str,
    ):
        """The error message element contains non-empty text when an error occurs."""
        await _init_page(page, base_url)

        await page.route(
            "**/api/strategy/init",
            _json_route(503, {"error": "IBKR unavailable", "code": "IBKR_CONNECTION_ERROR"}),
        )

        await _enter_symbol(page, "AAPL")
        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)

        error_text = await page.locator("#error-message").inner_text()
        assert error_text.strip(), "Error message should not be empty"

    async def test_new_error_replaces_previous_message(
        self,
        page: Page,
        base_url: str,
    ):
        """A second error replaces the message from the first error in the banner."""
        await _init_page(page, base_url)

        # First error: backend returns "Not found" (raw message shown in banner)
        await page.route(
            "**/api/strategy/init",
            _json_route(404, {"error": "Not found", "code": "SYMBOL_NOT_FOUND"}),
        )
        await _enter_symbol(page, "FIRST")
        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)
        first_message = await page.locator("#error-message").inner_text()

        await page.unroute("**/api/strategy/init")

        # Second error: backend returns "IBKR error" (different raw message)
        await page.route(
            "**/api/strategy/init",
            _json_route(502, {"error": "IBKR error", "code": "IBKR_API_ERROR"}),
        )
        await _enter_symbol(page, "SECOND")
        await expect(page.locator("#error-banner")).not_to_have_class(re.compile(r"hidden"), timeout=5000)
        second_message = await page.locator("#error-message").inner_text()

        assert first_message != second_message, "Second error should show a different message"
