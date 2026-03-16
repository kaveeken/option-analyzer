/**
 * Volatility Chart Component
 *
 * Renders rolling realized volatility (RV) over a 3-year history on a canvas,
 * with current implied volatility (IV 30d) as a horizontal reference line.
 * RV window is configurable: 30d, days-to-expiry, or custom.
 */
class VolatilityChart {
    constructor() {
        this._data = null;   // { closes, currentIv, symbol }
        this._window = 30;   // RV window in trading days
        this._mode = '30d';  // '30d' | 'dte' | 'custom'
        this._canvas = null;
        this._section = null;
        this._initialized = false;
    }

    init() {
        this._section = getById('vol-chart-section');
        this._canvas = getById('vol-chart-canvas');

        if (!this._section || !this._canvas) return;

        const btn30 = getById('rv-window-30d');
        const btnDte = getById('rv-window-dte');
        const customInput = getById('rv-window-input');

        if (btn30) on(btn30, 'click', () => this._setMode('30d'));
        if (btnDte) on(btnDte, 'click', () => this._setMode('dte'));
        if (customInput) {
            on(customInput, 'focus', () => this._setMode('custom'));
            on(customInput, 'input', () => {
                if (this._mode === 'custom') {
                    const v = parseInt(customInput.value);
                    if (v >= 2) {
                        this._window = v;
                        this._render();
                    }
                }
            });
        }

        // React to state changes
        state.subscribe((newState, changedKeys) => {
            if (changedKeys.includes('volHistory')) {
                if (newState.volHistory) {
                    this._data = newState.volHistory;
                    if (this._mode === 'dte' && newState.targetDate) {
                        this._window = this._daysToExpiry(newState.targetDate);
                    }
                    this._render();
                    show(this._section);
                } else {
                    hide(this._section);
                }
            }
            if (changedKeys.includes('targetDate') && this._mode === 'dte' && newState.targetDate) {
                this._window = this._daysToExpiry(newState.targetDate);
                if (this._data) this._render();
            }
        });

        window.addEventListener('resize', () => {
            if (this._data) this._render();
        });

        this._initialized = true;
    }

    _setMode(mode) {
        this._mode = mode;

        const btn30 = getById('rv-window-30d');
        const btnDte = getById('rv-window-dte');
        const customInput = getById('rv-window-input');

        [btn30, btnDte].forEach(b => b && b.classList.remove('active'));
        customInput && customInput.classList.remove('active');

        if (mode === '30d') {
            btn30 && btn30.classList.add('active');
            this._window = 30;
        } else if (mode === 'dte') {
            btnDte && btnDte.classList.add('active');
            const targetDate = state.get('targetDate');
            this._window = targetDate ? this._daysToExpiry(targetDate) : 30;
        } else if (mode === 'custom') {
            customInput && customInput.classList.add('active');
            const v = parseInt(customInput ? customInput.value : '30');
            this._window = (v >= 2) ? v : 30;
        }

        if (this._data) this._render();
    }

    /**
     * Approximate days until standard options expiry (3rd Friday) for a month code.
     * @param {string} targetDate - e.g. "JAN26"
     * @returns {number} Calendar days from today to expiry (minimum 2)
     */
    _daysToExpiry(targetDate) {
        if (!targetDate || targetDate.length < 5) return 30;
        const MONTHS = { JAN:0, FEB:1, MAR:2, APR:3, MAY:4, JUN:5, JUL:6, AUG:7, SEP:8, OCT:9, NOV:10, DEC:11 };
        const mo = MONTHS[targetDate.slice(0, 3).toUpperCase()];
        const yr = 2000 + parseInt(targetDate.slice(3));
        if (mo === undefined || isNaN(yr)) return 30;

        // 3rd Friday of the month
        const firstDay = new Date(yr, mo, 1);
        const dow = firstDay.getDay(); // 0=Sun, 5=Fri
        const firstFriday = 1 + ((5 - dow + 7) % 7);
        const expDate = new Date(yr, mo, firstFriday + 14);

        const today = new Date();
        const days = Math.round((expDate - today) / (1000 * 60 * 60 * 24));
        return Math.max(2, days);
    }

    /**
     * Compute rolling annualized realized volatility from daily closes.
     * @param {Array} closes - [{date, close}] sorted oldest-first
     * @param {number} window - Number of trading days
     * @returns {Array} [{date, rv}] where rv is annualized % std dev of log returns
     */
    _computeRV(closes, window) {
        if (closes.length < window + 2) return [];

        // Compute log returns
        const logReturns = [];
        for (let i = 0; i < closes.length - 1; i++) {
            logReturns.push({
                date: closes[i + 1].date,
                r: Math.log(closes[i + 1].close / closes[i].close),
            });
        }

        // Rolling std dev, annualized
        const result = [];
        for (let i = window - 1; i < logReturns.length; i++) {
            const slice = logReturns.slice(i - window + 1, i + 1).map(x => x.r);
            const mean = slice.reduce((a, b) => a + b, 0) / slice.length;
            const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / (slice.length - 1);
            result.push({ date: logReturns[i].date, rv: Math.sqrt(variance * 252) * 100 });
        }
        return result;
    }

    _render() {
        if (!this._data || !this._canvas) return;

        const { closes, currentIv } = this._data;
        const rvData = this._computeRV(closes, this._window);
        if (rvData.length === 0) return;

        // Size canvas to its CSS display width
        const W = this._canvas.offsetWidth || 600;
        const H = 220;
        this._canvas.width = W * (window.devicePixelRatio || 1);
        this._canvas.height = H * (window.devicePixelRatio || 1);
        this._canvas.style.width = W + 'px';
        this._canvas.style.height = H + 'px';

        const ctx = this._canvas.getContext('2d');
        ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
        ctx.clearRect(0, 0, W, H);

        const PAD = { top: 20, right: 20, bottom: 32, left: 50 };
        const chartW = W - PAD.left - PAD.right;
        const chartH = H - PAD.top - PAD.bottom;

        // Y bounds — round up to nearest 10, with headroom
        const maxVal = Math.max(...rvData.map(d => d.rv), currentIv ?? 0);
        const yMax = Math.ceil(maxVal / 10) * 10 + 10;
        const yMin = 0;

        // X bounds
        const timestamps = rvData.map(d => new Date(d.date).getTime());
        const xMin = timestamps[0];
        const xMax = timestamps[timestamps.length - 1];

        const xScale = t => PAD.left + ((t - xMin) / (xMax - xMin)) * chartW;
        const yScale = v => PAD.top + chartH - ((v - yMin) / (yMax - yMin)) * chartH;

        const gridColor = '#e2e8f0';
        const textColor = '#64748b';
        const rvColor = '#3b82f6';
        const ivColor = '#f97316';

        ctx.font = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

        // Y-axis grid lines and labels (every 10%)
        for (let v = 0; v <= yMax; v += 10) {
            const y = yScale(v);
            ctx.strokeStyle = gridColor;
            ctx.lineWidth = 1;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(PAD.left, y);
            ctx.lineTo(PAD.left + chartW, y);
            ctx.stroke();

            ctx.fillStyle = textColor;
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';
            ctx.fillText(v + '%', PAD.left - 6, y);
        }

        // X-axis: yearly tick marks and labels
        const startYear = new Date(xMin).getFullYear();
        const endYear = new Date(xMax).getFullYear();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        for (let yr = startYear; yr <= endYear; yr++) {
            const t = new Date(yr, 0, 1).getTime();
            if (t < xMin || t > xMax) continue;
            const x = xScale(t);

            ctx.strokeStyle = gridColor;
            ctx.lineWidth = 1;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(x, PAD.top);
            ctx.lineTo(x, PAD.top + chartH);
            ctx.stroke();

            ctx.fillStyle = textColor;
            ctx.fillText(yr, x, PAD.top + chartH + 5);
        }

        // RV filled area
        ctx.beginPath();
        rvData.forEach((d, i) => {
            const x = xScale(timestamps[i]);
            const y = yScale(d.rv);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        ctx.lineTo(xScale(timestamps[timestamps.length - 1]), PAD.top + chartH);
        ctx.lineTo(xScale(timestamps[0]), PAD.top + chartH);
        ctx.closePath();
        ctx.fillStyle = rvColor + '22';
        ctx.fill();

        // RV line
        ctx.beginPath();
        rvData.forEach((d, i) => {
            const x = xScale(timestamps[i]);
            const y = yScale(d.rv);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        ctx.strokeStyle = rvColor;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([]);
        ctx.stroke();

        // Current IV reference line
        if (currentIv != null) {
            const y = yScale(currentIv);
            ctx.beginPath();
            ctx.moveTo(PAD.left, y);
            ctx.lineTo(PAD.left + chartW, y);
            ctx.strokeStyle = ivColor;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([5, 4]);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.fillStyle = ivColor;
            ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'bottom';
            ctx.fillText('IV ' + currentIv.toFixed(1) + '%', PAD.left + chartW - 4, y - 2);
        }

        // Legend (top-left inside chart area)
        ctx.font = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
        const lx = PAD.left + 8;
        const ly = PAD.top + 8;

        ctx.strokeStyle = rvColor;
        ctx.lineWidth = 2;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(lx, ly + 5);
        ctx.lineTo(lx + 14, ly + 5);
        ctx.stroke();

        ctx.fillStyle = textColor;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText('RV ' + this._window + 'd', lx + 18, ly + 5);
    }
}

const volatilityChart = new VolatilityChart();
