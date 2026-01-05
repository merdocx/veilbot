const initUsersPage = () => {
    // Инициализируем live-поиск
    if (typeof window.initLiveSearch === 'function') {
        window.initLiveSearch({
            pageUrl: '/users',
            tableSelector: '#users-table',
            statsSelector: '.stats-grid',
            paginationSelector: '.pagination',
        });
    } else {
        console.warn('[VeilBot][users] initLiveSearch недоступен, загружаем скрипт...');
        const script = document.createElement('script');
        script.src = '/static/js/live-search.js';
        script.onload = () => {
            window.initLiveSearch({
                pageUrl: '/users',
                tableSelector: '#users-table',
                statsSelector: '.stats-grid',
                paginationSelector: '.pagination',
            });
        };
        document.head.appendChild(script);
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUsersPage);
} else {
    initUsersPage();
}

export {};

