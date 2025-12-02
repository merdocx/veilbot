const initTariffsPage = () => {
    // Инициализируем live-поиск
    if (typeof window.initLiveSearch === 'function') {
        window.initLiveSearch({
            pageUrl: '/tariffs',
            tableSelector: '#tariffs-table',
        });
    } else {
        console.warn('[VeilBot][tariffs] initLiveSearch недоступен');
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTariffsPage);
} else {
    initTariffsPage();
}

export {};

