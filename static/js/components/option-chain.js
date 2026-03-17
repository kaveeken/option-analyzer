/**
 * Option Chain Table Component
 *
 * Displays option chain with calls and puts, allows adding positions
 */

// In-flight chain load controller — module-level so symbol changes can abort it
let _chainAbortController = null;

/**
 * Abort any in-flight chain load (called when symbol changes)
 */
function abortChainLoad() {
    if (_chainAbortController) {
        _chainAbortController.abort();
        _chainAbortController = null;
    }
}

/**
 * Initialize option chain component
 */
function initOptionChain() {
    const monthSelector = getById('month-selector');
    const monthLoad = getById('month-load');
    const optionChainSection = getById('option-chain-section');

    if (!monthSelector || !monthLoad) {
        console.error('Option chain elements not found');
        return;
    }

    function setLoadingUI(loading) {
        if (loading) {
            monthLoad.textContent = 'Cancel';
            monthLoad.disabled = false;
            monthLoad.classList.replace('btn-primary', 'btn-secondary');
        } else {
            monthLoad.textContent = 'Load Chain';
            monthLoad.disabled = !monthSelector.value;
            monthLoad.classList.replace('btn-secondary', 'btn-primary');
        }
    }

    // Enable/disable load button based on selection
    on(monthSelector, 'change', () => {
        if (!_chainAbortController) {
            monthLoad.disabled = !monthSelector.value;
        }
    });

    // Handle load/cancel button click
    on(monthLoad, 'click', async () => {
        // If loading, this click is a cancel
        if (_chainAbortController) {
            abortChainLoad();
            setLoadingUI(false);
            return;
        }

        const symbol = state.get('symbol');
        const month = monthSelector.value;
        const currentTargetDate = state.get('targetDate');

        if (!symbol || !month) {
            showError('Please select an expiration month');
            return;
        }

        _chainAbortController = new AbortController();
        const signal = _chainAbortController.signal;
        setLoadingUI(true);

        try {
            // Update target date if it's different from current
            if (month !== currentTargetDate) {
                await updateTargetDate(month, signal);
            }

            // Load option chain from API
            await getOptionChain(symbol, month, signal);

            // Show option chain section
            show(optionChainSection);
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('Failed to load option chain:', error);
            }
        } finally {
            _chainAbortController = null;
            setLoadingUI(false);
        }
    });

    // Subscribe to state changes
    state.subscribe((newState, changedKeys) => {
        if (changedKeys.includes('chainLoading')) {
            const indicator = getById('chain-loading-indicator');
            if (indicator) {
                newState.chainLoading ? show(indicator) : hide(indicator);
            }
            if (newState.chainLoading) {
                show(optionChainSection);
            }
        }

        // Render table when option chain is loaded
        if (changedKeys.includes('optionChain')) {
            if (newState.optionChain) {
                renderOptionChainTable(newState.optionChain);
                show(optionChainSection);
            }
        }

        // Re-render when positions change to update highlights
        if (changedKeys.includes('positions')) {
            if (newState.optionChain) {
                renderOptionChainTable(newState.optionChain);
            }
        }
    });
}

/**
 * Merge calls and puts by strike price
 * @param {Array} calls - Array of call option contracts
 * @param {Array} puts - Array of put option contracts
 * @returns {Array} Merged array sorted by strike
 */
function mergeOptionsByStrike(calls, puts) {
    const strikeMap = new Map();

    // Add calls to map
    calls.forEach(call => {
        if (!strikeMap.has(call.strike)) {
            strikeMap.set(call.strike, { strike: call.strike });
        }
        strikeMap.get(call.strike).call = call;
    });

    // Add puts to map
    puts.forEach(put => {
        if (!strikeMap.has(put.strike)) {
            strikeMap.set(put.strike, { strike: put.strike });
        }
        strikeMap.get(put.strike).put = put;
    });

    // Convert to array and sort by strike
    return Array.from(strikeMap.values()).sort((a, b) => a.strike - b.strike);
}

/**
 * Check if a contract is in current positions
 * @param {number} conid - Contract ID
 * @returns {Object|null} Position object if found, null otherwise
 */
function findPositionByConid(conid) {
    const positions = state.get('positions') || [];
    return positions.find(pos => pos.conid === conid) || null;
}

/**
 * Render option chain table
 * @param {Object} optionChain - Option chain data with calls and puts
 */
function renderOptionChainTable(optionChain) {
    const tableBody = getById('option-chain-body');

    if (!tableBody) {
        return;
    }

    clearChildren(tableBody);

    const merged = mergeOptionsByStrike(optionChain.calls, optionChain.puts);

    merged.forEach(row => {
        const tr = createElement('tr');

        // Call columns: Bid | Ask | Δ | IV% | Add
        if (row.call) {
            tr.appendChild(createPriceCell(row.call.bid));
            tr.appendChild(createPriceCell(row.call.ask));
            tr.appendChild(createDeltaCell(row.call.delta, 'C'));
            tr.appendChild(createIVCell(row.call.implied_volatility));
            tr.appendChild(createActionCell(row.call, 'C'));
        } else {
            for (let i = 0; i < 5; i++) tr.appendChild(createElement('td', {}, '—'));
        }

        // Strike column (with expand chevron)
        tr.appendChild(createStrikeCell(row.strike));

        // Put columns: Add | Δ | IV% | Bid | Ask
        if (row.put) {
            tr.appendChild(createActionCell(row.put, 'P'));
            tr.appendChild(createDeltaCell(row.put.delta, 'P'));
            tr.appendChild(createIVCell(row.put.implied_volatility));
            tr.appendChild(createPriceCell(row.put.bid));
            tr.appendChild(createPriceCell(row.put.ask));
        } else {
            for (let i = 0; i < 5; i++) tr.appendChild(createElement('td', {}, '—'));
        }

        tableBody.appendChild(tr);

        // Expandable subrow for Gamma/Theta/Vega (hidden by default)
        const hasGreeks = (row.call && (row.call.gamma != null || row.call.theta != null || row.call.vega != null)) ||
                          (row.put  && (row.put.gamma  != null || row.put.theta  != null || row.put.vega  != null));
        if (hasGreeks) {
            const subrow = createGreeksSubrow(row.call, row.put);
            subrow.classList.add('hidden');
            tableBody.appendChild(subrow);

            // Wire up the chevron button in the strike cell to toggle subrow
            const chevron = tr.querySelector('.expand-row-btn');
            if (chevron) {
                on(chevron, 'click', (e) => {
                    e.stopPropagation();
                    const expanded = !subrow.classList.contains('hidden');
                    if (expanded) {
                        subrow.classList.add('hidden');
                        chevron.textContent = '›';
                        chevron.classList.remove('expanded');
                    } else {
                        subrow.classList.remove('hidden');
                        chevron.textContent = '›';
                        chevron.classList.add('expanded');
                    }
                });
            }
        }
    });
}

/**
 * Create delta cell with color gradient based on ITM depth
 * @param {number|null} delta - Delta value
 * @param {string} right - 'C' or 'P'
 * @returns {HTMLElement} Table cell
 */
function createDeltaCell(delta, right) {
    if (delta == null) {
        return createElement('td', { class: 'delta-cell' }, '—');
    }
    const td = createElement('td', { class: 'delta-cell' }, formatNumber(delta, 2));
    // Color by ITM depth: calls positive (green), puts negative (red), ATM near 0 = neutral
    const abs = Math.abs(delta);
    if (right === 'C') {
        const intensity = Math.min(1, abs * 2); // 0.5 delta = full color
        td.style.color = `rgb(${Math.round(22 + (1 - intensity) * 50)}, ${Math.round(163 - intensity * 40)}, ${Math.round(74 - intensity * 20)})`;
    } else {
        const intensity = Math.min(1, abs * 2);
        td.style.color = `rgb(${Math.round(220 - intensity * 20)}, ${Math.round(38 + (1 - intensity) * 40)}, ${Math.round(38)})`;
    }
    return td;
}

/**
 * Create implied volatility cell
 * @param {number|null} iv - Implied volatility percentage
 * @returns {HTMLElement} Table cell
 */
function createIVCell(iv) {
    const value = iv != null ? formatNumber(iv, 1) + '%' : '—';
    return createElement('td', { class: 'iv-cell' }, value);
}

/**
 * Create expandable subrow showing Gamma, Theta, Vega for call and put
 * @param {Object|null} call - Call contract
 * @param {Object|null} put - Put contract
 * @returns {HTMLElement} Table row
 */
function createGreeksSubrow(call, put) {
    const tr = createElement('tr', { class: 'greeks-subrow' });
    const td = createElement('td', { colspan: '11', class: 'greeks-subrow-cell' });

    const fmt = v => v != null ? formatNumber(v, 4) : '—';

    const inner = createElement('div', { class: 'greeks-subrow-inner' });

    if (call) {
        const callDiv = createElement('div', { class: 'greeks-subrow-side greeks-subrow-call' });
        callDiv.appendChild(createElement('span', { class: 'greek-label' }, 'Γ'));
        callDiv.appendChild(createElement('span', { class: 'greek-val' }, fmt(call.gamma)));
        callDiv.appendChild(createElement('span', { class: 'greek-label' }, 'Θ'));
        callDiv.appendChild(createElement('span', { class: 'greek-val' }, fmt(call.theta)));
        callDiv.appendChild(createElement('span', { class: 'greek-label' }, 'V'));
        callDiv.appendChild(createElement('span', { class: 'greek-val' }, fmt(call.vega)));
        inner.appendChild(callDiv);
    }

    const spacer = createElement('div', { class: 'greeks-subrow-spacer' });
    inner.appendChild(spacer);

    if (put) {
        const putDiv = createElement('div', { class: 'greeks-subrow-side greeks-subrow-put' });
        putDiv.appendChild(createElement('span', { class: 'greek-label' }, 'Γ'));
        putDiv.appendChild(createElement('span', { class: 'greek-val' }, fmt(put.gamma)));
        putDiv.appendChild(createElement('span', { class: 'greek-label' }, 'Θ'));
        putDiv.appendChild(createElement('span', { class: 'greek-val' }, fmt(put.theta)));
        putDiv.appendChild(createElement('span', { class: 'greek-label' }, 'V'));
        putDiv.appendChild(createElement('span', { class: 'greek-val' }, fmt(put.vega)));
        inner.appendChild(putDiv);
    }

    td.appendChild(inner);
    tr.appendChild(td);
    return tr;
}

/**
 * Create price cell with formatted value
 * @param {number|null} price - Price value
 * @returns {HTMLElement} Table cell
 */
function createPriceCell(price) {
    const value = price !== null && price !== undefined
        ? formatPrice(price, 2)
        : '-';
    return createElement('td', {}, value);
}

/**
 * Create strike price cell with expand chevron
 * @param {number} strike - Strike price
 * @returns {HTMLElement} Table cell with strike-price class
 */
function createStrikeCell(strike) {
    const td = createElement('td', { class: 'strike-price' });
    const chevron = createElement('button', { class: 'expand-row-btn', title: 'Show Γ Θ V' }, '›');
    td.appendChild(chevron);
    td.appendChild(document.createTextNode(formatStrike(strike)));
    return td;
}

/**
 * Create action cell with Add button
 * @param {Object} contract - Option contract data
 * @param {string} right - 'C' for call, 'P' for put
 * @returns {HTMLElement} Table cell with action buttons
 */
function createActionCell(contract, right) {
    const td = createElement('td');

    // Check if this contract is already in positions
    const existingPosition = findPositionByConid(contract.conid);

    if (existingPosition) {
        // Show indicator that position exists
        const indicator = createElement(
            'span',
            { class: 'position-indicator' },
            `${formatQuantity(existingPosition.quantity)}`
        );
        td.appendChild(indicator);
    } else {
        // Show Add button
        const addButton = createElement(
            'button',
            {
                class: 'btn-add-position',
                'data-conid': contract.conid,
                'data-strike': contract.strike,
                'data-right': right,
            },
            'Add'
        );

        // Click handler to add position
        on(addButton, 'click', async (e) => {
            e.preventDefault();
            const conid = parseInt(addButton.dataset.conid);

            // Prompt for quantity
            const quantityStr = prompt('Enter quantity (positive for long, negative for short):', '1');
            if (quantityStr === null) {
                return; // User cancelled
            }

            const quantity = parseInt(quantityStr);
            if (isNaN(quantity) || quantity === 0) {
                showError('Invalid quantity. Must be a non-zero integer.');
                return;
            }

            try {
                await addPosition(conid, quantity);
            } catch (error) {
                // Error already handled by API client
                console.error('Failed to add position:', error);
            }
        });

        td.appendChild(addButton);
    }

    return td;
}

/**
 * Clear option chain display
 */
function clearOptionChain() {
    const tableBody = getById('option-chain-body');
    const optionChainSection = getById('option-chain-section');

    if (tableBody) {
        clearChildren(tableBody);
    }

    if (optionChainSection) {
        hide(optionChainSection);
    }
}
