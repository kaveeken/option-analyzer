/**
 * Reactive State Management
 *
 * Singleton AppState that manages application data and UI state with
 * a reactive subscription system for automatic UI updates.
 */

class AppState {
    constructor() {
        if (AppState.instance) {
            return AppState.instance;
        }

        // Core application data
        this._state = {
            // Stock data
            symbol: null,
            currentPrice: null,
            conid: null,
            availableExpirations: [],
            targetDate: null,
            iv_30d: null,
            hist_vol: null,
            iv_hv_ratio: null,
            dividends_forward: null,
            dividends_ttm: null,

            // Volatility history (fetched separately after symbol load)
            volHistory: null, // { closes: [{date, close}], currentIv: number|null, symbol: string }

            // Option chain data
            optionChain: null, // { expiration, calls: [], puts: [] }

            // Strategy data
            positions: [], // Array of PositionResponse objects
            stockQuantity: 0,

            // Analysis results
            analysis: null, // { price_distribution, expected_value, probability_of_profit, max_gain, max_loss, plot_url }

            // UI state
            isLoading: false,
            loadingMessage: '',
            error: null,
            autoAnalyze: true,

            // Session
            sessionId: null,
        };

        // Subscribers - functions called when state changes
        this._subscribers = [];

        AppState.instance = this;
    }

    /**
     * Get the current state (read-only copy)
     * @returns {Object} Current state
     */
    getState() {
        return { ...this._state };
    }

    /**
     * Get a specific state value
     * @param {string} key - State key to retrieve
     * @returns {*} State value
     */
    get(key) {
        return this._state[key];
    }

    /**
     * Update state and notify subscribers
     * @param {Object} updates - Partial state object with updates
     */
    setState(updates) {
        // Track which keys changed
        const changedKeys = [];

        // Apply updates and track changes
        for (const [key, value] of Object.entries(updates)) {
            if (this._state[key] !== value) {
                this._state[key] = value;
                changedKeys.push(key);
            }
        }

        // Only notify if something actually changed
        if (changedKeys.length > 0) {
            this._notify(changedKeys);
        }
    }

    /**
     * Subscribe to state changes
     * @param {Function} callback - Function called when state changes: (state, changedKeys) => void
     * @returns {Function} Unsubscribe function
     */
    subscribe(callback) {
        this._subscribers.push(callback);

        // Return unsubscribe function
        return () => {
            const index = this._subscribers.indexOf(callback);
            if (index > -1) {
                this._subscribers.splice(index, 1);
            }
        };
    }

    /**
     * Notify all subscribers of state changes
     * @private
     * @param {string[]} changedKeys - Array of state keys that changed
     */
    _notify(changedKeys) {
        const state = this.getState();
        this._subscribers.forEach(callback => {
            try {
                callback(state, changedKeys);
            } catch (error) {
                console.error('Error in state subscriber:', error);
            }
        });
    }

    /**
     * Reset state to initial values
     */
    reset() {
        this.setState({
            symbol: null,
            currentPrice: null,
            conid: null,
            availableExpirations: [],
            targetDate: null,
            optionChain: null,
            positions: [],
            stockQuantity: 0,
            analysis: null,
            isLoading: false,
            loadingMessage: '',
            error: null,
            iv_30d: null,
            hist_vol: null,
            iv_hv_ratio: null,
            dividends_forward: null,
            dividends_ttm: null,
            volHistory: null,
        });
    }

    /**
     * Set loading state
     * @param {boolean} isLoading - Whether app is loading
     * @param {string} message - Loading message
     */
    setLoading(isLoading, message = '') {
        this.setState({
            isLoading,
            loadingMessage: message,
        });
    }

    /**
     * Set error state
     * @param {string|null} error - Error message or null to clear
     */
    setError(error) {
        this.setState({ error });
    }

    /**
     * Clear error state
     */
    clearError() {
        this.setState({ error: null });
    }

    /**
     * Initialize strategy with stock data
     * @param {Object} data - Strategy init response
     */
    initStrategy(data) {
        this.setState({
            symbol: data.symbol,
            currentPrice: data.current_price,
            targetDate: data.target_date,
            availableExpirations: data.available_expirations,
            sessionId: data.session_id,
            positions: [],
            stockQuantity: 0,
            optionChain: null,
            analysis: null,
            iv_30d: data.iv_30d ?? null,
            hist_vol: data.hist_vol ?? null,
            iv_hv_ratio: data.iv_hv_ratio ?? null,
            dividends_forward: data.dividends_forward ?? null,
            dividends_ttm: data.dividends_ttm ?? null,
            volHistory: null, // cleared until history fetch completes
        });
    }

    /**
     * Update stock information
     * @param {Object} data - Stock response
     */
    updateStock(data) {
        this.setState({
            symbol: data.symbol,
            currentPrice: data.current_price,
            conid: data.conid,
            availableExpirations: data.available_expirations,
        });
    }

    /**
     * Update option chain
     * @param {Object} data - Option chain response
     */
    updateOptionChain(data) {
        this.setState({
            optionChain: data,
        });
    }

    /**
     * Update positions
     * @param {Array} positions - Array of position objects
     */
    updatePositions(positions) {
        this.setState({
            positions: positions || [],
        });
    }

    /**
     * Update stock quantity
     * @param {number} quantity - Number of shares
     */
    updateStockQuantity(quantity) {
        this.setState({
            stockQuantity: quantity,
        });
    }

    /**
     * Update analysis results
     * @param {Object} data - Analysis response
     */
    updateAnalysis(data) {
        this.setState({
            analysis: data,
        });
    }

    /**
     * Update target date
     * @param {string} targetDate - New target expiration month
     */
    updateTargetDate(targetDate) {
        this.setState({
            targetDate,
        });
    }

    /**
     * Check if strategy has been initialized
     * @returns {boolean} True if strategy is initialized
     */
    hasStrategy() {
        return this._state.symbol !== null && this._state.sessionId !== null;
    }

    /**
     * Check if option chain is loaded
     * @returns {boolean} True if option chain is loaded
     */
    hasOptionChain() {
        return this._state.optionChain !== null;
    }

    /**
     * Check if there are any positions
     * @returns {boolean} True if there are positions
     */
    hasPositions() {
        return this._state.positions.length > 0;
    }

    /**
     * Check if analysis is available
     * @returns {boolean} True if analysis is available
     */
    hasAnalysis() {
        return this._state.analysis !== null;
    }
}

// Export singleton instance
const state = new AppState();
