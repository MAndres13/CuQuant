// The server refreshes Yahoo at most once per minute; quote timestamps are from Yahoo.
document.addEventListener('DOMContentLoaded', () => {
    const status = document.querySelector('[data-feed-status]');
    const indicator = document.querySelector('[data-feed-indicator]');
    const setText = (selector, text) => {
        document.querySelectorAll(selector).forEach(el => { el.textContent = text; });
    };
    const money = value => Number.isFinite(value) ? '$' + value.toFixed(4) : 'Unavailable';
    const connectionStatus = text => {
        if (status) status.textContent = text;
        if (indicator) indicator.classList.remove('status-live');
    };
    const update = payload => {
        const data = payload && payload.market_data;
        if (!data) return;
        const available = data.status !== 'unavailable' && Number.isFinite(data.price);
        setText('[data-price-display]', available ? money(data.price) : 'Unavailable');
        setText('[data-high-display]', available ? money(data.high) : 'N/A');
        setText('[data-low-display]', available ? money(data.low) : 'N/A');
        setText('[data-volume-display]', available ? String(data.volume) : 'N/A');
        setText('[data-timestamp-display]', data.timestamp || 'Unavailable');
        const change = document.querySelector('[data-change-display]');
        if (change) {
            change.textContent = available
                ? money(data.change) + ' (' + (data.change_percent >= 0 ? '+' : '') + data.change_percent.toFixed(2) + '%)'
                : 'Waiting for a Yahoo Finance quote';
            change.classList.toggle('positive', available && data.change_percent > 0);
            change.classList.toggle('negative', available && data.change_percent < 0);
        }
        connectionStatus(!available ? 'Yahoo Finance unavailable' :
            data.status === 'stale' ? 'Yahoo Finance unavailable — showing last saved quote' :
            'Yahoo Finance • latest available quote (may be delayed)');
    };
    if (typeof io !== 'function') {
        connectionStatus('Price updates unavailable — connection library failed to load');
        return;
    }
    const socket = io();
    socket.on('initial_data', update);
    socket.on('market_update', update);
    socket.on('connect', () => connectionStatus('Connected — waiting for quote'));
    socket.on('disconnect', () => connectionStatus('Disconnected — displayed quote is not updating'));
    socket.on('connect_error', () => connectionStatus('Connection error — retrying'));
});
