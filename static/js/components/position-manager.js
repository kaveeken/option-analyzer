/**
 * Position Manager Component
 *
 * Manages option positions and stock quantity
 */

let stockQuantityTimeout = null;

/**
 * Initialize position manager component
 */
function initPositionManager() {
    const stockQuantityInput = getById('stock-quantity-input');
    const stockQuantityUpdate = getById('stock-quantity-update');
    const resetButton = getById('reset-strategy');

    // Handle stock quantity update
    if (stockQuantityInput && stockQuantityUpdate) {
        on(stockQuantityUpdate, 'click', async () => {
            const quantity = parseInt(stockQuantityInput.value) || 0;

            try {
                await updateStockQuantity(quantity);
            } catch (error) {
                console.error('Failed to update stock quantity:', error);
            }
        });

        // Enter key in input
        on(stockQuantityInput, 'keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                stockQuantityUpdate.click();
            }
        });
    }

    // Handle reset strategy button
    if (resetButton) {
        on(resetButton, 'click', async () => {
            const confirmed = confirm(
                'Are you sure you want to reset the strategy? This will delete all positions and clear the stock quantity.'
            );

            if (!confirmed) {
                return;
            }

            try {
                await resetStrategy();

                // Clear stock quantity input
                if (stockQuantityInput) {
                    stockQuantityInput.value = '';
                }
            } catch (error) {
                console.error('Failed to reset strategy:', error);
            }
        });
    }

    // Subscribe to state changes
    state.subscribe((newState, changedKeys) => {
        // Update positions table
        if (changedKeys.includes('positions')) {
            renderPositionsTable(newState.positions);
            updateResetButtonVisibility();
        }

        // Update stock quantity display
        if (changedKeys.includes('stockQuantity')) {
            renderStockQuantity(newState.stockQuantity);
        }

        // Update net greeks strip
        if (changedKeys.includes('positions') || changedKeys.includes('stockQuantity')) {
            renderNetGreeks(newState.positions, newState.stockQuantity);
        }

        // Show/hide stock quantity section when strategy is initialized
        if (changedKeys.includes('symbol')) {
            const stockQuantityContainer = getById('stock-quantity-container');
            if (newState.symbol && stockQuantityContainer) {
                show(stockQuantityContainer);
            }
        }
    });
}

/**
 * Render positions table
 * @param {Array} positions - Array of position objects
 */
function renderPositionsTable(positions) {
    const emptyPositions = getById('empty-positions');
    const positionsTable = getById('positions-table');
    const positionsBody = getById('positions-body');

    if (!emptyPositions || !positionsTable || !positionsBody) {
        return;
    }

    // Clear existing rows
    clearChildren(positionsBody);

    // Show empty state or table
    if (!positions || positions.length === 0) {
        show(emptyPositions);
        hide(positionsTable);
        return;
    }

    hide(emptyPositions);
    show(positionsTable);

    // Render each position
    positions.forEach(position => {
        const tr = createPositionRow(position);
        positionsBody.appendChild(tr);
    });
}

/**
 * Create a position table row
 * @param {Object} position - Position data
 * @returns {HTMLElement} Table row
 */
function createPositionRow(position) {
    const tr = createElement('tr');

    // Type (Call/Put)
    const typeCell = createElement('td');
    const typeBadge = createElement(
        'span',
        { class: `position-type ${position.right === 'C' ? 'call' : 'put'}` },
        formatPositionType(position.right)
    );
    typeCell.appendChild(typeBadge);
    tr.appendChild(typeCell);

    // Strike
    tr.appendChild(createElement('td', {}, formatStrike(position.strike)));

    // Quantity
    const qtyCell = createElement('td');
    const qtyText = formatQuantity(position.quantity);
    setText(qtyCell, qtyText);
    // Add color based on long/short
    if (position.quantity > 0) {
        addClass(qtyCell, 'text-success');
    } else if (position.quantity < 0) {
        addClass(qtyCell, 'text-error');
    }
    tr.appendChild(qtyCell);

    // Bid
    tr.appendChild(createElement('td', {}, formatPrice(position.bid, 2)));

    // Ask
    tr.appendChild(createElement('td', {}, formatPrice(position.ask, 2)));

    // Actions
    const actionsCell = createElement('td');
    const actionsDiv = createElement('div', { class: 'action-buttons' });

    // Modify button
    const modifyBtn = createElement(
        'button',
        { class: 'btn-modify' },
        'Modify'
    );
    on(modifyBtn, 'click', () => handleModifyPosition(position));
    actionsDiv.appendChild(modifyBtn);

    // Delete button
    const deleteBtn = createElement(
        'button',
        { class: 'btn-delete' },
        'Delete'
    );
    on(deleteBtn, 'click', () => handleDeletePosition(position));
    actionsDiv.appendChild(deleteBtn);

    actionsCell.appendChild(actionsDiv);
    tr.appendChild(actionsCell);

    return tr;
}

/**
 * Handle modify position
 * @param {Object} position - Position to modify
 */
async function handleModifyPosition(position) {
    const currentQty = position.quantity;
    const quantityStr = prompt(
        `Modify quantity for ${formatStrike(position.strike)} ${formatPositionType(position.right)}:\n` +
        `Current: ${formatQuantity(currentQty)}\n` +
        `Enter new quantity (positive for long, negative for short):`,
        currentQty
    );

    if (quantityStr === null) {
        return; // User cancelled
    }

    const newQuantity = parseInt(quantityStr);
    if (isNaN(newQuantity) || newQuantity === 0) {
        showError('Invalid quantity. Must be a non-zero integer.');
        return;
    }

    try {
        await modifyPosition(position.conid, newQuantity);
    } catch (error) {
        console.error('Failed to modify position:', error);
    }
}

/**
 * Handle delete position
 * @param {Object} position - Position to delete
 */
async function handleDeletePosition(position) {
    const confirmed = confirm(
        `Delete position: ${formatStrike(position.strike)} ${formatPositionType(position.right)} ${formatQuantity(position.quantity)}?`
    );

    if (!confirmed) {
        return;
    }

    try {
        await deletePosition(position.conid);
    } catch (error) {
        console.error('Failed to delete position:', error);
    }
}

/**
 * Render stock quantity display
 * @param {number} quantity - Stock quantity
 */
function renderStockQuantity(quantity) {
    const stockQuantityDisplay = getById('stock-quantity-display');
    const stockQuantityValue = getById('stock-quantity-value');

    if (!stockQuantityDisplay || !stockQuantityValue) {
        return;
    }

    if (quantity !== 0) {
        const text = Math.abs(quantity) + ' share' + (Math.abs(quantity) !== 1 ? 's' : '');
        const direction = quantity > 0 ? ' (long)' : ' (short)';
        setText(stockQuantityValue, text + direction);
        show(stockQuantityDisplay);
    } else {
        hide(stockQuantityDisplay);
    }
}

/**
 * Update reset button visibility
 * Show if there are positions or stock quantity
 */
function updateResetButtonVisibility() {
    const resetButton = getById('reset-strategy');
    const positions = state.get('positions') || [];
    const stockQuantity = state.get('stockQuantity') || 0;

    if (!resetButton) {
        return;
    }

    if (positions.length > 0 || stockQuantity !== 0) {
        show(resetButton);
    } else {
        hide(resetButton);
    }
}

/**
 * Render net greeks strip below positions table
 * Sums quantity * greek * multiplier across all positions, adds stock delta contribution
 * @param {Array} positions - Array of position objects
 * @param {number} stockQuantity - Number of stock shares
 */
function renderNetGreeks(positions, stockQuantity) {
    const strip = getById('net-greeks-strip');
    if (!strip) return;

    const pos = positions || [];
    const sqty = stockQuantity || 0;

    // Check if any positions have greek data
    const hasAny = pos.some(p => p.delta != null || p.gamma != null || p.theta != null || p.vega != null);
    if (!hasAny && sqty === 0) {
        hide(strip);
        return;
    }

    let netDelta = sqty; // stock delta contribution: 1 per share
    let netGamma = 0;
    let netTheta = 0;
    let netVega = 0;
    let hasGreeks = sqty !== 0;

    pos.forEach(p => {
        const mult = p.multiplier || 100;
        if (p.delta != null) { netDelta += p.quantity * p.delta * mult; hasGreeks = true; }
        if (p.gamma != null) { netGamma += p.quantity * p.gamma * mult; hasGreeks = true; }
        if (p.theta != null) { netTheta += p.quantity * p.theta * mult; hasGreeks = true; }
        if (p.vega  != null) { netVega  += p.quantity * p.vega  * mult; hasGreeks = true; }
    });

    if (!hasGreeks) {
        hide(strip);
        return;
    }

    const fmtG = v => (v >= 0 ? '+' : '') + formatNumber(v, 2);
    setText(getById('net-delta'), fmtG(netDelta));
    setText(getById('net-gamma'), fmtG(netGamma));
    setText(getById('net-theta'), fmtG(netTheta));
    setText(getById('net-vega'),  fmtG(netVega));
    show(strip);
}

/**
 * Clear position manager display
 */
function clearPositionManager() {
    const positionsBody = getById('positions-body');
    const emptyPositions = getById('empty-positions');
    const positionsTable = getById('positions-table');
    const stockQuantityInput = getById('stock-quantity-input');
    const stockQuantityDisplay = getById('stock-quantity-display');

    if (positionsBody) {
        clearChildren(positionsBody);
    }

    if (emptyPositions) {
        show(emptyPositions);
    }

    if (positionsTable) {
        hide(positionsTable);
    }

    if (stockQuantityInput) {
        stockQuantityInput.value = '';
    }

    if (stockQuantityDisplay) {
        hide(stockQuantityDisplay);
    }

    const netGreeksStrip = getById('net-greeks-strip');
    if (netGreeksStrip) {
        hide(netGreeksStrip);
    }

    updateResetButtonVisibility();
}
