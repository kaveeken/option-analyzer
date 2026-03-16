/**
 * Symbol Input Component
 *
 * Handles stock symbol input, initialization, and display
 */

/**
 * Initialize symbol input component
 */
function initSymbolInput() {
    const symbolInput = getById('symbol-input');
    const symbolSubmit = getById('symbol-submit');
    const stockInfo = getById('stock-info');
    const monthSection = getById('month-section');

    if (!symbolInput || !symbolSubmit) {
        console.error('Symbol input elements not found');
        return;
    }

    // Handle form submission
    const handleSubmit = async () => {
        const symbol = symbolInput.value.trim().toUpperCase();

        if (!symbol) {
            showError('Please enter a stock symbol');
            return;
        }

        try {
            // Initialize strategy with the symbol
            const data = await initStrategy(symbol);

            // Render stock information
            renderStockInfo(data);

            // Populate month dropdown
            populateMonthDropdown(data.available_expirations, data.target_date);

            // Show month section
            show(monthSection);

            // Clear input
            symbolInput.value = '';
        } catch (error) {
            // Error already handled by API client
            console.error('Failed to initialize strategy:', error);
        }
    };

    // Submit button click
    on(symbolSubmit, 'click', handleSubmit);

    // Enter key in input
    on(symbolInput, 'keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleSubmit();
        }
    });

    // Subscribe to state changes to update UI
    state.subscribe((newState, changedKeys) => {
        const stockKeys = ['symbol', 'currentPrice', 'iv_30d', 'hist_vol', 'iv_hv_ratio', 'dividends_forward', 'dividends_ttm'];
        if (stockKeys.some(k => changedKeys.includes(k))) {
            if (newState.symbol) {
                renderStockInfo(newState);
            }
        }

        if (changedKeys.includes('availableExpirations')) {
            if (newState.availableExpirations && newState.availableExpirations.length > 0) {
                populateMonthDropdown(newState.availableExpirations, newState.targetDate);
                show(monthSection);
            }
        }
    });
}

/**
 * Render stock information display
 * @param {Object} data - Stock data (API response or state object)
 */
function renderStockInfo(data) {
    const stockInfo = getById('stock-info');
    const stockSymbol = getById('stock-symbol');
    const stockPrice = getById('stock-price');

    if (!stockInfo || !stockSymbol || !stockPrice) {
        return;
    }

    // Support both API snake_case and state camelCase keys
    const price = data.current_price ?? data.currentPrice;
    const iv30d = data.iv_30d;
    const histVol = data.hist_vol;
    const ivHv = data.iv_hv_ratio;
    const divFwd = data.dividends_forward;
    const divTtm = data.dividends_ttm;

    setText(stockSymbol, data.symbol);
    setText(stockPrice, formatCurrency(price));

    // Volatility group
    const volGroup = getById('stock-vol-group');
    if (volGroup && (iv30d != null || histVol != null || ivHv != null)) {
        const fmt = v => v != null ? formatNumber(v, 1) + '%' : '—';
        setText(getById('stock-iv30d'), fmt(iv30d));
        setText(getById('stock-histvol'), fmt(histVol));
        setText(getById('stock-ivhv'), fmt(ivHv));
        show(volGroup);
    } else if (volGroup) {
        hide(volGroup);
    }

    // Dividend group
    const divGroup = getById('stock-div-group');
    if (divGroup && (divFwd != null || divTtm != null)) {
        const fmtDiv = v => v != null ? '$' + formatNumber(v, 2) : '—';
        setText(getById('stock-div-fwd'), fmtDiv(divFwd));
        setText(getById('stock-div-ttm'), fmtDiv(divTtm));
        const yield_ = (divFwd != null && price) ? formatNumber((divFwd / price) * 100, 2) + '%' : '—';
        setText(getById('stock-div-yield'), yield_);
        show(divGroup);
    } else if (divGroup) {
        hide(divGroup);
    }

    show(stockInfo);
}

/**
 * Populate month selector dropdown
 * @param {string[]} expirations - Available expiration months
 * @param {string} selectedMonth - Currently selected month
 */
function populateMonthDropdown(expirations, selectedMonth) {
    const monthSelector = getById('month-selector');

    if (!monthSelector) {
        return;
    }

    // Clear existing options
    clearChildren(monthSelector);

    // Add placeholder option
    const placeholder = createElement('option', { value: '' }, 'Select expiration month');
    monthSelector.appendChild(placeholder);

    // Add expiration options
    expirations.forEach(month => {
        const option = createElement('option', { value: month }, month);
        if (month === selectedMonth) {
            option.selected = true;
        }
        monthSelector.appendChild(option);
    });

    // If a month is selected, enable the load button
    const monthLoad = getById('month-load');
    if (monthLoad) {
        monthLoad.disabled = !selectedMonth;
    }
}

/**
 * Clear symbol input and related UI
 */
function clearSymbolInput() {
    const symbolInput = getById('symbol-input');
    const stockInfo = getById('stock-info');
    const monthSection = getById('month-section');

    if (symbolInput) {
        symbolInput.value = '';
    }

    if (stockInfo) {
        hide(stockInfo);
    }

    if (monthSection) {
        hide(monthSection);
    }
}
