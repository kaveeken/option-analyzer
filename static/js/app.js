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

    console.log('Option Returns Analyzer - Ready');
});
