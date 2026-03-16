/**
 * API Client Module
 *
 * Wrapper functions for backend API endpoints.
 * Each method automatically updates AppState with response data.
 */

const API_BASE = '/api';

/**
 * Generic fetch wrapper with error handling
 * @private
 * @param {string} url - API endpoint URL
 * @param {Object} options - Fetch options
 * @returns {Promise<Object>} Response data
 * @throws {Error} API error with message and code
 */
async function apiFetch(url, options = {}) {
    const defaultOptions = {
        credentials: 'include', // Include session cookies
        headers: {
            'Content-Type': 'application/json',
        },
    };

    const response = await fetch(url, { ...defaultOptions, ...options });

    // Parse JSON response
    const data = await response.json();

    // Handle error responses
    if (!response.ok) {
        const error = new Error(data.error || 'API request failed');
        error.code = data.code;
        error.status = response.status;
        error.details = data.details;
        throw error;
    }

    return data;
}

/**
 * Initialize a new trading strategy with a stock symbol
 * @param {string} symbol - Stock ticker symbol (e.g., "AAPL")
 * @returns {Promise<Object>} Strategy initialization response
 */
async function initStrategy(symbol) {
    state.setLoading(true, `Loading ${symbol}...`);

    try {
        const data = await apiFetch(`${API_BASE}/strategy/init`, {
            method: 'POST',
            body: JSON.stringify({ symbol: symbol.toUpperCase() }),
        });

        // Update state with strategy data
        state.initStrategy(data);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Get stock information by symbol
 * @param {string} symbol - Stock ticker symbol
 * @returns {Promise<Object>} Stock response
 */
async function getStock(symbol) {
    state.setLoading(true, `Fetching ${symbol} data...`);

    try {
        const data = await apiFetch(`${API_BASE}/stocks/${symbol.toUpperCase()}`);

        // Update state with stock data
        state.updateStock(data);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Get option chain for a symbol and expiration month
 * @param {string} symbol - Stock ticker symbol
 * @param {string} month - Expiration month (e.g., "JAN26")
 * @returns {Promise<Object>} Option chain response
 */
async function getOptionChain(symbol, month) {
    state.setLoading(true, `Loading option chain for ${month}...`);

    try {
        const data = await apiFetch(
            `${API_BASE}/stocks/${symbol.toUpperCase()}/chains?month=${month.toUpperCase()}`
        );

        // Update state with option chain data
        state.updateOptionChain(data);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Get current strategy summary
 * @returns {Promise<Object>} Strategy summary response
 */
async function getStrategySummary() {
    try {
        const data = await apiFetch(`${API_BASE}/strategy`);

        // Update state with strategy data
        state.setState({
            symbol: data.symbol,
            currentPrice: data.current_price,
            targetDate: data.target_date,
            availableExpirations: data.available_expirations,
            stockQuantity: data.stock_quantity,
            positions: data.positions,
            iv_30d: data.iv_30d ?? null,
            hist_vol: data.hist_vol ?? null,
            iv_hv_ratio: data.iv_hv_ratio ?? null,
            dividends_forward: data.dividends_forward ?? null,
            dividends_ttm: data.dividends_ttm ?? null,
        });

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    }
}

/**
 * Add an option position to the strategy
 * @param {number} conid - IBKR contract identifier
 * @param {number} quantity - Number of contracts (positive=long, negative=short)
 * @returns {Promise<Object>} Updated positions response
 */
async function addPosition(conid, quantity) {
    state.setLoading(true, 'Adding position...');

    try {
        const data = await apiFetch(`${API_BASE}/strategy/positions`, {
            method: 'POST',
            body: JSON.stringify({ conid, quantity }),
        });

        // Update state with new positions
        state.updatePositions(data.positions);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Modify an existing position's quantity
 * @param {number} conid - IBKR contract identifier
 * @param {number} quantity - New quantity (positive=long, negative=short)
 * @returns {Promise<Object>} Updated positions response
 */
async function modifyPosition(conid, quantity) {
    state.setLoading(true, 'Modifying position...');

    try {
        const data = await apiFetch(`${API_BASE}/strategy/positions/${conid}`, {
            method: 'PATCH',
            body: JSON.stringify({ quantity }),
        });

        // Update state with modified positions
        state.updatePositions(data.positions);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Delete a position from the strategy
 * @param {number} conid - IBKR contract identifier
 * @returns {Promise<Object>} Updated positions response
 */
async function deletePosition(conid) {
    state.setLoading(true, 'Deleting position...');

    try {
        const data = await apiFetch(`${API_BASE}/strategy/positions/${conid}`, {
            method: 'DELETE',
        });

        // Update state with remaining positions
        state.updatePositions(data.positions);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Update the stock quantity in the strategy
 * @param {number} quantity - Number of shares (positive=long, negative=short, 0=none)
 * @returns {Promise<Object>} Updated strategy summary
 */
async function updateStockQuantity(quantity) {
    state.setLoading(true, 'Updating stock quantity...');

    try {
        const data = await apiFetch(`${API_BASE}/strategy/stock-quantity`, {
            method: 'PATCH',
            body: JSON.stringify({ stock_quantity: quantity }),
        });

        // Update state with new stock quantity and positions
        state.setState({
            stockQuantity: data.stock_quantity,
            positions: data.positions,
        });

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Update the target expiration date
 * @param {string} targetDate - New target expiration month (e.g., "FEB26")
 * @returns {Promise<Object>} Updated strategy response
 */
async function updateTargetDate(targetDate) {
    state.setLoading(true, 'Updating target date...');

    try {
        const data = await apiFetch(`${API_BASE}/strategy/target-date`, {
            method: 'PATCH',
            body: JSON.stringify({ target_date: targetDate }),
        });

        // Update state with new target date
        state.updateTargetDate(data.target_date);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Analyze the current strategy using Monte Carlo simulation
 * @returns {Promise<Object>} Analysis results
 */
async function analyzeStrategy() {
    state.setLoading(true, 'Running analysis...');

    try {
        const data = await apiFetch(`${API_BASE}/strategy/analyze`, {
            method: 'POST',
        });

        // Update state with analysis results
        state.updateAnalysis(data);

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Reset the current strategy
 * Clears all positions and resets to earliest expiration
 * @returns {Promise<Object>} Reset strategy summary
 */
async function resetStrategy() {
    state.setLoading(true, 'Resetting strategy...');

    try {
        const data = await apiFetch(`${API_BASE}/strategy/reset`, {
            method: 'POST',
        });

        // Update state with reset strategy data
        state.setState({
            positions: [],
            stockQuantity: 0,
            targetDate: data.target_date,
            analysis: null,
        });

        return data;
    } catch (error) {
        state.setError(error.message);
        throw error;
    } finally {
        state.setLoading(false);
    }
}

/**
 * Fetch 3 years of daily closes for realized volatility chart
 * Non-fatal: silently fails if history unavailable (e.g., outside market hours)
 * @param {string} symbol - Stock ticker symbol
 * @returns {Promise<Object|null>} History data or null on failure
 */
async function fetchVolatilityHistory(symbol) {
    try {
        const data = await apiFetch(`${API_BASE}/stocks/${symbol.toUpperCase()}/history`);
        state.setState({
            volHistory: {
                closes: data.closes,
                currentIv: data.current_iv,
                symbol: data.symbol,
            },
        });
        return data;
    } catch (error) {
        console.error('Failed to fetch volatility history:', error);
        // Non-fatal: chart stays hidden
        return null;
    }
}

/**
 * Health check endpoint
 * @returns {Promise<Object>} Health check response
 */
async function healthCheck() {
    try {
        return await apiFetch('/health');
    } catch (error) {
        console.error('Health check failed:', error);
        throw error;
    }
}
