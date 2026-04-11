"""
E2E tests for position management.

Tests cover:
- Adding multiple positions (calls and puts)
- Modifying position quantity
- Deleting individual positions
- Reset all positions
- Stock quantity input
- Position display format
"""

import re

import pytest
from playwright.async_api import Page, expect


@pytest.mark.asyncio
class TestAddingPositions:
    """Tests for adding option positions to a strategy."""

    async def test_add_call_position(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Adding a call position appears in the positions table."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
        )

        position_rows = page.locator("#positions-body tr")
        await expect(position_rows).to_have_count(1)
        await expect(position_rows.first).to_contain_text("Call")

    async def test_add_put_position(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Adding a put position appears in the positions table."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(200002, -1)],
        )

        position_rows = page.locator("#positions-body tr")
        await expect(position_rows).to_have_count(1)
        await expect(position_rows.first).to_contain_text("Put")

    async def test_add_multiple_positions(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Adding multiple positions results in the correct row count."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100001, 1), (100002, -2), (200002, 1)],
        )

        await expect(page.locator("#positions-body tr")).to_have_count(3)

    async def test_add_calls_and_puts(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Both call and put positions appear correctly in the table."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 2), (200002, -1)],
        )

        body = page.locator("#positions-body")
        await expect(body).to_contain_text("Call")
        await expect(body).to_contain_text("Put")

    async def test_positions_table_hidden_before_any_position(
        self,
        page: Page,
        setup_strategy,
    ):
        """Positions table is hidden and empty-state shown before any position is added."""
        await setup_strategy("AAPL")

        await expect(page.locator("#positions-table")).to_have_class(re.compile(r"hidden"))
        await expect(page.locator("#empty-positions")).to_be_visible()

    async def test_positions_table_visible_after_adding_position(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Positions table becomes visible once at least one position is added."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
        )

        await expect(page.locator("#positions-table")).not_to_have_class(re.compile(r"hidden"))
        await expect(page.locator("#empty-positions")).not_to_be_visible()


@pytest.mark.asyncio
class TestPositionDisplayFormat:
    """Tests for how positions are displayed in the table."""

    async def test_call_position_shows_strike_and_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Call position row shows the strike price and quantity."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 2)],  # ATM call, strike 150
        )

        row = page.locator("#positions-body tr").first
        await expect(row).to_contain_text("Call")
        await expect(row).to_contain_text("150")
        await expect(row).to_contain_text("2")

    async def test_put_position_shows_strike_and_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Put position row shows the strike price and quantity."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(200002, -1)],  # ATM put, strike 150
        )

        row = page.locator("#positions-body tr").first
        await expect(row).to_contain_text("Put")
        await expect(row).to_contain_text("150")
        await expect(row).to_contain_text("-1")

    async def test_position_row_has_modify_and_delete_buttons(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Each position row includes Modify and Delete action buttons."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
        )

        row = page.locator("#positions-body tr").first
        await expect(row.locator('button:has-text("Modify")')).to_be_visible()
        await expect(row.locator('button:has-text("Delete")')).to_be_visible()

    async def test_itm_otm_strikes_show_correct_strike_values(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Positions at different strikes display the correct strike value."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100001, 1), (100003, 1)],  # 140 and 160 strikes
        )

        body = page.locator("#positions-body")
        await expect(body).to_contain_text("140")
        await expect(body).to_contain_text("160")


@pytest.mark.asyncio
class TestModifyingPositions:
    """Tests for modifying position quantities."""

    async def test_modify_position_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Modifying a position updates the quantity shown in the table."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
        )

        row = page.locator("#positions-body tr").first
        modify_button = row.locator('button:has-text("Modify")')

        page.once("dialog", lambda dialog: dialog.accept("5"))
        await modify_button.click()
        await page.wait_for_timeout(500)

        await expect(row).to_contain_text("5")

    async def test_modify_to_negative_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """A position can be modified to a negative quantity (short position)."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
        )

        row = page.locator("#positions-body tr").first
        modify_button = row.locator('button:has-text("Modify")')

        page.once("dialog", lambda dialog: dialog.accept("-3"))
        await modify_button.click()
        await page.wait_for_timeout(500)

        await expect(row).to_contain_text("-3")

    async def test_modify_preserves_position_count(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Modifying one position does not change the total number of positions."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1), (200002, -1)],
        )

        rows = page.locator("#positions-body tr")
        await expect(rows).to_have_count(2)

        first_row = rows.first
        page.once("dialog", lambda dialog: dialog.accept("4"))
        await first_row.locator('button:has-text("Modify")').click()
        await page.wait_for_timeout(500)

        await expect(rows).to_have_count(2)


@pytest.mark.asyncio
class TestDeletingPositions:
    """Tests for deleting positions from a strategy."""

    async def test_delete_single_position_shows_empty_state(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Deleting the only position shows the empty-positions state."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
        )

        delete_button = page.locator("#positions-body tr").first.locator('button:has-text("Delete")')
        page.once("dialog", lambda dialog: dialog.accept())
        await delete_button.click()

        await expect(page.locator("#positions-table")).to_have_class(re.compile(r"hidden"), timeout=3000)
        await expect(page.locator("#empty-positions")).to_be_visible()

    async def test_delete_one_of_multiple_positions(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Deleting one position from a multi-position strategy leaves the rest."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 2), (200002, -1)],
        )

        rows = page.locator("#positions-body tr")
        await expect(rows).to_have_count(2)

        # Delete the first position (call)
        page.once("dialog", lambda dialog: dialog.accept())
        await rows.first.locator('button:has-text("Delete")').click()

        await expect(rows).to_have_count(1, timeout=3000)

    async def test_delete_call_leaves_put(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Deleting the call position leaves only the put in the table."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 2), (200002, -1)],
        )

        # Delete the first row (call)
        page.once("dialog", lambda dialog: dialog.accept())
        await page.locator("#positions-body tr").first.locator('button:has-text("Delete")').click()

        body = page.locator("#positions-body")
        await expect(body).not_to_contain_text("Call", timeout=3000)
        await expect(body).to_contain_text("Put")

    async def test_delete_all_positions_sequentially(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Deleting all positions one by one results in the empty state."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1), (200002, -1)],
        )

        for _ in range(2):
            page.once("dialog", lambda dialog: dialog.accept())
            await page.locator("#positions-body tr button:has-text('Delete')").first.click()
            await page.wait_for_timeout(500)

        await expect(page.locator("#empty-positions")).to_be_visible(timeout=3000)
        await expect(page.locator("#positions-table")).to_have_class(re.compile(r"hidden"))


@pytest.mark.asyncio
class TestResetAllPositions:
    """Tests for the reset all positions functionality."""

    async def test_reset_clears_all_positions(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Reset all removes every position and shows the empty state."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100001, 1), (100002, -2), (200002, 1)],
        )

        await expect(page.locator("#positions-body tr")).to_have_count(3)

        page.once("dialog", lambda dialog: dialog.accept())
        await page.locator("#reset-strategy").click()

        await expect(page.locator("#positions-table")).to_have_class(re.compile(r"hidden"), timeout=3000)
        await expect(page.locator("#empty-positions")).to_be_visible()

    async def test_reset_clears_stock_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Reset all also resets the stock quantity display to hidden."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
            stock_quantity=100,
        )

        await expect(page.locator("#stock-quantity-display")).not_to_have_class(re.compile(r"hidden"))

        page.once("dialog", lambda dialog: dialog.accept())
        await page.locator("#reset-strategy").click()

        await expect(page.locator("#stock-quantity-display")).to_have_class(
            re.compile(r"hidden"), timeout=3000
        )

    async def test_reset_with_single_position(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Reset all works correctly even with just one position."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
        )

        page.once("dialog", lambda dialog: dialog.accept())
        await page.locator("#reset-strategy").click()

        await expect(page.locator("#empty-positions")).to_be_visible(timeout=3000)


@pytest.mark.asyncio
class TestStockQuantity:
    """Tests for the stock quantity input."""

    async def test_set_positive_stock_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Setting a positive stock quantity shows the quantity display."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
            stock_quantity=100,
        )

        stock_qty_display = page.locator("#stock-quantity-display")
        await expect(stock_qty_display).not_to_have_class(re.compile(r"hidden"))
        await expect(page.locator("#stock-quantity-value")).to_contain_text("100")

    async def test_set_negative_stock_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Setting a negative stock quantity (short shares) is reflected in the display."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
            stock_quantity=-50,
        )

        await expect(page.locator("#stock-quantity-value")).to_contain_text("short")

    async def test_stock_quantity_display_hidden_initially(
        self,
        page: Page,
        setup_strategy,
    ):
        """Stock quantity display is hidden before a quantity is set."""
        await setup_strategy("AAPL")

        await expect(page.locator("#stock-quantity-display")).to_have_class(re.compile(r"hidden"))

    async def test_update_stock_quantity(
        self,
        page: Page,
        setup_strategy_with_positions,
    ):
        """Updating the stock quantity changes the displayed value."""
        await setup_strategy_with_positions(
            symbol="AAPL",
            month="15JAN26",
            positions=[(100002, 1)],
            stock_quantity=100,
        )

        await expect(page.locator("#stock-quantity-value")).to_contain_text("100")

        # Update to a new value
        await page.fill("#stock-quantity-input", "200")
        await page.click("#stock-quantity-update")
        await page.wait_for_timeout(500)

        await expect(page.locator("#stock-quantity-value")).to_contain_text("200")
