const initVipHandlers = () => {
    // Используем делегирование событий для надежной работы даже после обновления DOM
    // Находим таблицу и слушаем события на ней
    const table = document.querySelector('#users-table');
    if (!table) {
        console.warn('[VeilBot][users] Users table not found');
        return;
    }
    
    // Удаляем старый обработчик, если есть
    if (table._vipHandler) {
        table.removeEventListener('change', table._vipHandler);
    }
    
    // Создаем новый обработчик с делегированием
    table._vipHandler = async (e) => {
        // Проверяем, что клик был на VIP чекбоксе
        if (!e.target || !e.target.classList.contains('vip-checkbox')) {
            return;
        }
            const userId = e.target.dataset.userId;
            const isChecked = e.target.checked;
            
            if (!userId) {
                console.error('[VeilBot][users] No user ID found');
                return;
            }

            // Получаем CSRF токен из мета-тега или скрытого поля
            let csrfToken = document.querySelector('meta[name="csrf-token"]')?.content
                || document.querySelector('input[name="csrf_token"]')?.value
                || document.querySelector('[name="csrf_token"]')?.value;
            
            // Если токен не найден, попробуем получить его через глобальную переменную или из формы
            if (!csrfToken) {
                // Пробуем найти в любом скрытом input на странице
                const csrfInput = document.querySelector('input[type="hidden"][name*="csrf"]');
                if (csrfInput) {
                    csrfToken = csrfInput.value;
                }
            }
            
            if (!csrfToken) {
                console.error('[VeilBot][users] CSRF token not found. Available inputs:', 
                    Array.from(document.querySelectorAll('input[type="hidden"]')).map(i => i.name));
                alert('Ошибка: CSRF токен не найден. Перезагрузите страницу.');
                e.target.checked = !isChecked; // Откатываем изменение
                return;
            }
            
            console.log('[VeilBot][users] Toggling VIP for user', userId, 'with token:', csrfToken.substring(0, 10) + '...');

            // Отправляем запрос
            try {
                const formData = new FormData();
                formData.append('csrf_token', csrfToken);

                const response = await fetch(`/users/${userId}/toggle-vip`, {
                    method: 'POST',
                    body: formData,
                });

                const data = await response.json();
                
                console.log('[VeilBot][users] VIP toggle response:', data);

                if (!response.ok || !data.success) {
                    throw new Error(data.error || 'Ошибка при изменении VIP статуса');
                }

                console.log('[VeilBot][users] VIP status updated successfully, reloading page...');
                
                // Обновляем страницу, чтобы получить актуальные данные с сервера
                // Это гарантирует, что состояние будет синхронизировано
                window.location.reload();
            } catch (error) {
                console.error('[VeilBot][users] Error toggling VIP status:', error);
                alert('Ошибка при изменении VIP статуса: ' + error.message);
                e.target.checked = !isChecked; // Откатываем изменение
            }
    };
    
    // Добавляем обработчик на таблицу с делегированием
    table.addEventListener('change', table._vipHandler);
    console.log('[VeilBot][users] VIP handlers initialized with event delegation');
};

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
    
    // Инициализируем обработчики VIP
    initVipHandlers();
    
    // Переинициализируем обработчики VIP после обновления таблицы через live-search
    document.addEventListener('tableUpdated', (event) => {
        if (event.detail && event.detail.tableSelector === '#users-table') {
            console.log('[VeilBot][users] Table updated, reinitializing VIP handlers');
            initVipHandlers();
        }
    });
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUsersPage);
} else {
    initUsersPage();
}

export {};

