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

    // Обработчик VIP чекбоксов
    const vipCheckboxes = document.querySelectorAll('.vip-checkbox');
    vipCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', async (e) => {
            const userId = e.target.dataset.userId;
            const isChecked = e.target.checked;
            
            if (!userId) {
                console.error('[VeilBot][users] No user ID found');
                return;
            }

            // Получаем CSRF токен из мета-тега или скрытого поля
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content
                || document.querySelector('input[name="csrf_token"]')?.value;
            
            if (!csrfToken) {
                console.error('[VeilBot][users] CSRF token not found');
                alert('Ошибка: CSRF токен не найден. Перезагрузите страницу.');
                e.target.checked = !isChecked; // Откатываем изменение
                return;
            }

            // Отправляем запрос
            try {
                const formData = new FormData();
                formData.append('csrf_token', csrfToken);

                const response = await fetch(`/users/${userId}/toggle-vip`, {
                    method: 'POST',
                    body: formData,
                });

                const data = await response.json();

                if (!response.ok || !data.success) {
                    throw new Error(data.error || 'Ошибка при изменении VIP статуса');
                }

                // Обновляем визуальное отображение
                const row = e.target.closest('tr');
                const userCell = row.querySelector('td:first-child .cell-primary');
                
                if (data.is_vip) {
                    // Добавляем иконку звездочки, если её нет
                    if (!userCell.querySelector('.material-icons')) {
                        const starIcon = document.createElement('span');
                        starIcon.className = 'material-icons icon-small';
                        starIcon.style.cssText = 'color: #FFD700; vertical-align: middle;';
                        starIcon.title = 'VIP пользователь';
                        starIcon.textContent = 'star';
                        userCell.insertBefore(starIcon, userCell.firstChild);
                    }
                } else {
                    // Удаляем иконку звездочки
                    const starIcon = userCell.querySelector('.material-icons');
                    if (starIcon) {
                        starIcon.remove();
                    }
                }

                // Показываем уведомление
                if (typeof window.VeilBotCommon !== 'undefined' && window.VeilBotCommon.showNotification) {
                    window.VeilBotCommon.showNotification(
                        `VIP статус ${data.is_vip ? 'установлен' : 'снят'}`,
                        'success'
                    );
                } else {
                    console.log(`VIP статус ${data.is_vip ? 'установлен' : 'снят'}`);
                }
            } catch (error) {
                console.error('[VeilBot][users] Error toggling VIP status:', error);
                e.target.checked = !isChecked; // Откатываем изменение
                alert(`Ошибка: ${error.message}`);
            }
        });
    });
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUsersPage);
} else {
    initUsersPage();
}

export {};

