/**
 * Main Application Entry Point
 *
 * Initializes all components when DOM is ready
 */

// Wait for DOM to be fully loaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('Option Returns Analyzer - Initializing...');

    // Initialize error handling first
    initErrorHandling();

    // Initialize components
    initSymbolInput();
    volatilityChart.init();
    initOptionChain();
    initPositionManager();
    initAnalysis();

    // Fetch and display version info
    fetch('/health')
        .then(res => res.json())
        .then(data => {
            const el = document.getElementById('version-display');
            if (el && data.commit) {
                el.textContent = data.version + ' @ ' + data.commit;
            }
        })
        .catch(err => console.error('Failed to fetch version:', err));

    console.log('Option Returns Analyzer - Ready');
});
