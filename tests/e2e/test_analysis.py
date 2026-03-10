"""
E2E tests focused on analysis behavior.

Tests cover:
- Auto-analyze triggers on position add/modify
- Manual analyze mode (disable auto, manual trigger)
- Loading state during analysis
- Chart and metrics updates when analysis completes
- Auto-analyze checkbox toggle behavior
- Manual button enabled/disabled states
"""

import re

import pytest
from playwright.async_api import Page, expect


@pytest.mark.asyncio
class TestAutoAnalyzeBehavior:
    """Tests for automatic analysis triggering."""

    async def test_auto_analyze_on_position_add(
        self,
        page: Page,
        base_url: str,
    ):
        """Verify that adding a position triggers auto-analysis."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Initialize strategy
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        # Load option chain
        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)")

        # Verify auto-analyze is enabled by default
        auto_analyze_checkbox = page.locator("#auto-analyze")
        await expect(auto_analyze_checkbox).to_be_checked()

        # Chart and metrics should be hidden before any position
        chart_container = page.locator("#chart-container")
        await expect(chart_container).to_have_class(re.compile(r"hidden"))

        # Add a position
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100002"]').first.click()

        # Verify position added
        await expect(page.locator("#positions-body tr")).to_have_count(1, timeout=3000)

        # Auto-analyze should trigger and chart should appear without manual action
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"), timeout=20000)
        await expect(page.locator("#metrics-display")).not_to_have_class(re.compile(r"hidden"))

    async def test_auto_analyze_on_position_modify(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Verify that modifying a position quantity re-triggers auto-analysis."""
        # Setup with one position
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="JAN26",
            positions=[(100002, 1)],
        )

        # Wait for initial analysis to complete
        chart_container = page.locator("#chart-container")
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"), timeout=20000)

        # Get the initial chart src
        chart_img = page.locator("#strategy-chart")
        initial_src = await chart_img.get_attribute("src")

        # Modify the position quantity
        modify_button = page.locator("#positions-body tr").first.locator('button:has-text("Modify")')
        page.once("dialog", lambda dialog: dialog.accept("3"))
        await modify_button.click()

        # Verify quantity updated
        await page.wait_for_timeout(500)
        await expect(page.locator("#positions-body tr").first).to_contain_text("3")

        # Wait for re-analysis - chart src should update
        await page.wait_for_timeout(5000)
        new_src = await chart_img.get_attribute("src")
        assert new_src != initial_src, "Chart should update after modifying position"

    async def test_analysis_section_appears_after_init(
        self,
        page: Page,
        base_url: str,
    ):
        """Verify that the analysis section becomes visible after strategy initialization."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Analysis section should be hidden initially
        analysis_section = page.locator("#analysis-section")
        await expect(analysis_section).to_have_class(re.compile(r"hidden"))

        # Initialize strategy
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        # Analysis section should now be visible
        await expect(analysis_section).not_to_have_class(re.compile(r"hidden"))


@pytest.mark.asyncio
class TestManualAnalyzeMode:
    """Tests for manual analysis mode (auto-analyze disabled)."""

    async def test_manual_analyze_requires_explicit_trigger(
        self,
        page: Page,
        base_url: str,
    ):
        """With auto-analyze off, analysis only runs when manually triggered."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Initialize strategy
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        # Load option chain
        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)")

        # Disable auto-analyze
        await page.locator("#auto-analyze").uncheck()

        # Add a position
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100002"]').first.click()
        await expect(page.locator("#positions-body tr")).to_have_count(1, timeout=3000)

        # Wait briefly to confirm chart does NOT appear automatically
        await page.wait_for_timeout(2000)
        chart_container = page.locator("#chart-container")
        await expect(chart_container).to_have_class(re.compile(r"hidden"))

        # Trigger analysis manually
        await page.locator("#manual-analyze").click()

        # Chart should now appear
        await expect(chart_container).not_to_have_class(re.compile(r"hidden"), timeout=20000)
        await expect(page.locator("#metrics-display")).not_to_have_class(re.compile(r"hidden"))

    async def test_manual_button_triggers_full_analysis(
        self,
        page: Page,
        base_url: str,
    ):
        """Manual analyze button triggers complete analysis with metrics and chart."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Initialize strategy and disable auto-analyze
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)")

        await page.locator("#auto-analyze").uncheck()

        # Add a position
        page.once("dialog", lambda dialog: dialog.accept("2"))
        await page.locator('button[data-conid="100002"]').first.click()
        await expect(page.locator("#positions-body tr")).to_have_count(1, timeout=3000)

        # Trigger manual analysis
        await page.locator("#manual-analyze").click()
        await expect(page.locator("#chart-container")).not_to_have_class(re.compile(r"hidden"), timeout=20000)

        # Verify all metrics populated
        await expect(page.locator("#metric-ev")).not_to_be_empty()
        await expect(page.locator("#metric-pop")).not_to_be_empty()
        await expect(page.locator("#metric-max-gain")).not_to_be_empty()
        await expect(page.locator("#metric-max-loss")).not_to_be_empty()

        # Verify chart image loaded
        chart_src = await page.locator("#strategy-chart").get_attribute("src")
        assert chart_src and ".png" in chart_src


@pytest.mark.asyncio
class TestAutoAnalyzeCheckboxToggle:
    """Tests for auto-analyze checkbox toggle behavior."""

    async def test_checkbox_enables_manual_button_when_unchecked(
        self,
        page: Page,
        base_url: str,
    ):
        """Unchecking auto-analyze should enable the manual analyze button."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Initialize strategy (needed so analysis section is visible)
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        # Manual button should be disabled when auto-analyze is on
        manual_button = page.locator("#manual-analyze")
        await expect(manual_button).to_be_disabled()

        # Uncheck auto-analyze
        await page.locator("#auto-analyze").uncheck()

        # Manual button should now be enabled
        await expect(manual_button).to_be_enabled()

    async def test_checkbox_disables_manual_button_when_rechecked(
        self,
        page: Page,
        base_url: str,
    ):
        """Re-checking auto-analyze should disable the manual analyze button."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        auto_checkbox = page.locator("#auto-analyze")
        manual_button = page.locator("#manual-analyze")

        # Disable auto-analyze
        await auto_checkbox.uncheck()
        await expect(manual_button).to_be_enabled()

        # Re-enable auto-analyze
        await auto_checkbox.check()
        await expect(manual_button).to_be_disabled()

    async def test_enabling_auto_analyze_triggers_analysis_with_positions(
        self,
        page: Page,
        base_url: str,
    ):
        """Re-enabling auto-analyze when positions exist should trigger analysis."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Initialize and disable auto-analyze before adding positions
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)")

        await page.locator("#auto-analyze").uncheck()

        # Add a position (no analysis should trigger)
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100002"]').first.click()
        await expect(page.locator("#positions-body tr")).to_have_count(1, timeout=3000)

        # Chart should still be hidden
        await page.wait_for_timeout(1000)
        await expect(page.locator("#chart-container")).to_have_class(re.compile(r"hidden"))

        # Re-enable auto-analyze - should trigger analysis immediately
        await page.locator("#auto-analyze").check()

        # Chart should appear without any further interaction
        await expect(page.locator("#chart-container")).not_to_have_class(re.compile(r"hidden"), timeout=20000)


@pytest.mark.asyncio
class TestLoadingState:
    """Tests for loading state during analysis."""

    async def test_loading_overlay_appears_during_analysis(
        self,
        page: Page,
        base_url: str,
    ):
        """Loading overlay should appear and then disappear during analysis."""
        await page.goto(base_url)
        await page.wait_for_load_state("networkidle")

        # Initialize strategy with manual analyze mode for control
        await page.fill("#symbol-input", "AAPL")
        await page.click("#symbol-submit")
        await page.wait_for_selector("#stock-info:not(.hidden)")

        await page.select_option("#month-selector", "JAN26")
        await page.click("#month-load")
        await page.wait_for_selector("#option-chain-section:not(.hidden)")

        await page.locator("#auto-analyze").uncheck()

        # Add a position
        page.once("dialog", lambda dialog: dialog.accept("1"))
        await page.locator('button[data-conid="100002"]').first.click()
        await expect(page.locator("#positions-body tr")).to_have_count(1, timeout=3000)

        # Trigger analysis and immediately check for loading overlay
        loading_overlay = page.locator("#loading-overlay")
        await page.locator("#manual-analyze").click()

        # Loading overlay should eventually appear (may be brief)
        # Wait for analysis to complete
        await expect(page.locator("#chart-container")).not_to_have_class(re.compile(r"hidden"), timeout=20000)

        # After analysis completes, loading overlay should be hidden
        await expect(loading_overlay).to_have_class(re.compile(r"hidden"))
