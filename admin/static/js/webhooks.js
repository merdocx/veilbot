const initWebhooksPage = () => {
    // Инициализируем live-поиск
    if (typeof window.initLiveSearch === 'function') {
        window.initLiveSearch({
            pageUrl: '/webhooks',
            tableSelector: '#webhooks-table',
            paginationSelector: '.pagination',
        });
    } else {
        console.warn('[VeilBot][webhooks] initLiveSearch недоступен');
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWebhooksPage);
} else {
    initWebhooksPage();
}

export {};

