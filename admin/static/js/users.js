const initUsersPage = () => {
    if (window.VeilBotCommon && typeof window.VeilBotCommon.initTableSearch === 'function') {
        window.VeilBotCommon.initTableSearch({
            tableSelector: '#users-table',
        });
    } else {
        console.warn('[VeilBot][users] initTableSearch недоступен');
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUsersPage);
} else {
    initUsersPage();
}

export {};

