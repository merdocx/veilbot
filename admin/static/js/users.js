const initVipHandlers = () => {
    // Обработчик VIP чекбоксов
    const vipCheckboxes = document.querySelectorAll('.vip-checkbox');
    vipCheckboxes.forEach(checkbox => {
        // Удаляем старые обработчики
        const newCheckbox = checkbox.cloneNode(true);
        checkbox.parentNode.replaceChild(newCheckbox, checkbox);
        
        newCheckbox.addEventListener('change', async (e) => {
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
        });
    });
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
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUsersPage);
} else {
    initUsersPage();
}

export {};

