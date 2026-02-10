"""
E2E test for complete application workflow.

Tests the full user journey from entering a symbol to analyzing a strategy.

Prerequisites:
    - Server must be running at localhost:8000
    - Server should be configured to use FakeIBKRClient (via dependency override in conftest)

Run tests with:
    pytest tests/e2e/test_full_workflow.py --headed  # To see browser
    pytest tests/e2e/test_full_workflow.py           # Headless mode
"""

import re

import pytest
from playwright.async_api import Page, expect


@pytest.mark.asyncio
class TestFullWorkflow:
    """Test complete user workflow from symbol entry to analysis."""

    async def test_complete_workflow_happy_path(
        self,
        page: Page,
        base_url: str,
    ):
        """
        Test the complete user journey:
        - Enter symbol and verify stock info appears
        - Select month and verify option chain loads
        - Add position and verify it appears in list
        - Verify auto-analyze triggers and displays chart/metrics
        - Modify position quantity and verify update triggers re-analysis
        - Update stock quantity and verify re-analysis
        - Delete position and verify removal
        """
        # Step 1: Navigate to app
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Verify initial state - no stock info visible
        stock_info = page.locator("#stock-info")
        await expect(stock_info).to_have_class(re.compile(r"hidden"))

        # Step 2: Enter symbol and submit
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")

        # Verify stock info appears
        await expect(stock_info).not_to_have_class(re.compile(r"hidden"), timeout=5000)
        await expect(page.locator("#stock-symbol")).to_have_text("AAPL")
        await expect(page.locator("#stock-price")).to_contain_text("150")

        # Verify month selector becomes visible
        month_section = page.locator("#month-section")
        await expect(month_section).not_to_have_class(re.compile(r"hidden"))

        # Step 3: Select month and load option chain
        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")

        # Verify option chain appears
        chain_section = page.locator("#option-chain-section")
        await expect(chain_section).not_to_have_class(re.compile(r"hidden"), timeout=10000)

        # Verify chain has rows (3 strikes: 140, 150, 160)
        chain_rows = page.locator("#option-chain-body tr")
        await expect(chain_rows).to_have_count(3, timeout=5000)

        # Step 4: Add a call position (ATM 150 strike, conid 100002)
        # Setup dialog handler BEFORE clicking
        page.once("dialog", lambda dialog: dialog.accept("2"))

        # Click the Add button for the call at 150 strike
        await page.locator('button[data-conid="100002"]').first.click()

        # Verify position appears in positions table
        positions_table = page.locator("#positions-table")
        await expect(positions_table).not_to_have_class(re.compile(r"hidden"), timeout=3000)

        position_rows = page.locator("#positions-body tr")
        await expect(position_rows).to_have_count(1)

        # Verify position details
        first_position = position_rows.first
        await expect(first_position).to_contain_text("Call")
        await expect(first_position).to_contain_text("150")
        await expect(first_position).to_contain_text("2")

        # Verify analysis section becomes visible
        analysis_section = page.locator("#analysis-section")
        await expect(analysis_section).not_to_have_class(re.compile(r"hidden"))

        # Wait for auto-analysis to complete and chart to appear
        chart_container = page.locator("#chart-container")
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"), timeout=20000)

        # Verify metrics display
        metrics_display = page.locator("#metrics-display")
        await expect(metrics_display).not_to_have_class(re.compile(r"hidden"))

        # Verify metrics have values
        await expect(page.locator("#metric-ev")).not_to_be_empty()
        await expect(page.locator("#metric-pop")).not_to_be_empty()
        await expect(page.locator("#metric-max-gain")).not_to_be_empty()
        await expect(page.locator("#metric-max-loss")).not_to_be_empty()

        # Verify chart image is loaded
        chart_img = page.locator("#strategy-chart")
        await expect(chart_img).to_have_attribute("src", re.compile(r".+\.png"))

        # Step 5: Modify position quantity
        modify_button = first_position.locator('button:has-text("Modify")')
        page.once("dialog", lambda dialog: dialog.accept("3"))
        await modify_button.click()

        # Verify quantity updated
        await page.wait_for_timeout(500)
        await expect(first_position).to_contain_text("3")

        # Wait for re-analysis
        await page.wait_for_timeout(3000)
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"))

        # Step 6: Update stock quantity
        await page.fill("#stock-quantity-input", "100")
        await page.click("#stock-quantity-update")

        # Verify stock quantity display appears
        stock_qty_display = page.locator("#stock-quantity-display")
        await expect(stock_qty_display).not_to_have_class(re.compile(r"hidden"))
        await expect(page.locator("#stock-quantity-value")).to_contain_text("100")

        # Wait for re-analysis after stock quantity change
        await page.wait_for_timeout(3000)
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"))

        # Step 7: Add a second position (put at 150 strike)
        page.once("dialog", lambda dialog: dialog.accept("-1"))
        await page.locator('button[data-conid="200002"]').first.click()

        # Verify two positions now
        await expect(position_rows).to_have_count(2, timeout=3000)

        # Verify put position details
        second_position = position_rows.nth(1)
        await expect(second_position).to_contain_text("Put")
        await expect(second_position).to_contain_text("-1")

        # Step 8: Delete the first position (call)
        delete_button = first_position.locator('button:has-text("Delete")')
        page.once("dialog", lambda dialog: dialog.accept())
        await delete_button.click()

        # Verify only one position remains
        await expect(position_rows).to_have_count(1, timeout=3000)

        # Verify the remaining position is the put
        await expect(page.locator("#positions-body")).to_contain_text("Put")
        await expect(page.locator("#positions-body")).not_to_contain_text("Call")

        # Verify chart still visible with remaining position
        await page.wait_for_timeout(2000)
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"))

        # Step 9: Delete last position
        last_delete = page.locator("#positions-body tr button:has-text('Delete')").first
        page.once("dialog", lambda dialog: dialog.accept())
        await last_delete.click()

        # Verify empty state
        await expect(page.locator("#empty-positions")).to_be_visible(timeout=2000)
        await expect(positions_table).to_have_class(re.compile(r"hidden"))


    async def test_workflow_without_auto_analyze(
        self,
        page: Page,
        base_url: str,
    ):
        """
        Test workflow with auto-analyze disabled.
        User must manually trigger analysis.
        """
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Setup: Enter symbol
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        # Load option chain
        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)")

        # Disable auto-analyze
        auto_analyze_checkbox = page.locator("#auto-analyze")
        await auto_analyze_checkbox.uncheck()

        # Add a position
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100002"]').first.click()

        # Verify position added
        await page.wait_for_timeout(1000)
        await expect(page.locator("#positions-body tr")).to_have_count(1)

        # Verify chart does NOT appear automatically
        chart_container = page.locator("#chart-container")
        await expect(chart_container).to_have_class(re.compile(r"hidden"))

        # Manually trigger analysis
        manual_analyze_button = page.locator("#manual-analyze")
        await expect(manual_analyze_button).to_be_enabled()
        await manual_analyze_button.click()

        # Now chart should appear
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"), timeout=20000)
        await expect(page.locator("#metrics-display")).not_to_have_class(re.compile(r"hidden"))


    async def test_reset_all_workflow(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """
        Test the reset all functionality.
        """
        # Setup: Create strategy with multiple positions
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="JAN26",
            positions=[(100002, 2), (200002, -1)],
            stock_quantity=100,
        )

        # Verify positions exist
        await expect(page.locator("#positions-body tr")).to_have_count(2)

        # Verify stock quantity is set
        await page.wait_for_timeout(1000)
        await expect(page.locator("#stock-quantity-value")).to_contain_text("100")

        # Wait for analysis to complete
        await page.wait_for_timeout(3000)
        chart_container = page.locator("#chart-container")
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"))

        # Click reset all
        reset_button = page.locator("#reset-strategy")
        page.once("dialog", lambda dialog: dialog.accept())
        await reset_button.click()

        # Verify all positions cleared
        await expect(page.locator("#positions-table")).to_have_class(re.compile(r"hidden"), timeout=2000)
        await expect(page.locator("#empty-positions")).to_be_visible()

        # Verify stock quantity display hidden (quantity reset to 0)
        stock_qty_display = page.locator("#stock-quantity-display")
        await expect(stock_qty_display).to_have_class(re.compile(r"hidden"))


    async def test_multiple_strikes_workflow(
        self,
        page: Page,
        base_url: str,
    ):
        """
        Test adding positions at different strikes to build a complex strategy.
        """
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Setup strategy
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)")

        # Add call at 140 strike (ITM)
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100001"]').first.click()
        await page.wait_for_timeout(500)

        # Add call at 150 strike (ATM) - short
        page.once("dialog", lambda dialog: dialog.accept("-2"))
        await page.locator('button[data-conid="100002"]').first.click()
        await page.wait_for_timeout(500)

        # Add put at 150 strike (ATM)
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="200002"]').first.click()

        # Verify 3 positions
        await expect(page.locator("#positions-body tr")).to_have_count(3, timeout=3000)

        # Verify analysis triggered for complex strategy
        await page.wait_for_timeout(4000)
        await expect(page.locator("#chart-container")).not_to_have_class(re.compile(r"hidden"))

        # Verify metrics are displayed
        metrics = page.locator("#metrics-display")
        await expect(metrics).not_to_have_class(re.compile(r"hidden"))
        await expect(page.locator("#metric-ev")).not_to_be_empty()
        await expect(page.locator("#metric-pop")).not_to_be_empty()
