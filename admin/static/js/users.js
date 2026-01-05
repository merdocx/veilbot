const initUsersPage = () => {
    // Устанавливаем атрибуты СРАЗУ, чтобы common.js их увидел при инициализации
    const searchInput = document.getElementById('global-search');
    if (searchInput) {
        searchInput.setAttribute('data-server-search', '1');
        searchInput.setAttribute('data-auto-search', '1');
    }
    
    // Серверный поиск вместо клиентского (live-поиск без перезагрузки страницы)
    // Инициализируем поиск с задержкой, чтобы common.js успел проверить атрибуты
    setTimeout(() => {
        const searchForm = document.getElementById('search-form');
        const searchInputForInit = document.getElementById('global-search');
        const resetSearchBtn = document.getElementById('reset-search-btn');
    
        if (searchForm && searchInputForInit) {
            // Убеждаемся, что клиентский поиск не активен для этого элемента
            // Атрибуты уже установлены выше
            searchInputForInit.setAttribute('data-server-search', '1');
            searchInputForInit.setAttribute('data-auto-search', '1');
            
            let searchTimeout = null;
            
            // Используем searchInputForInit для всех операций
            const searchInput = searchInputForInit;
            
            const applySearchResponse = async (response) => {
                if (!response.ok) {
                    throw new Error(`Search request failed with status ${response.status}`);
                }
                const html = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');

                const newStats = doc.querySelector('.stats-grid');
                const newTableBody = doc.querySelector('#users-table tbody');
                const newPagination = doc.querySelector('.pagination');

                const currentStats = document.querySelector('.stats-grid');
                const currentTableBody = document.querySelector('#users-table tbody');
                const currentPagination = document.querySelector('.pagination');

                if (newStats && currentStats) {
                    currentStats.replaceWith(newStats);
                }
                if (newTableBody && currentTableBody) {
                    currentTableBody.replaceWith(newTableBody);
                    // Переинициализируем обработчики VIP после обновления таблицы
                    initVipHandlers();
                }
                if (newPagination) {
                    if (currentPagination) {
                        currentPagination.replaceWith(newPagination);
                    } else {
                        // Если пагинации не было (одна страница), но теперь есть – добавляем после таблицы
                        const tableWrapper = document.querySelector('.table-scroll');
                        if (tableWrapper && tableWrapper.parentElement) {
                            tableWrapper.parentElement.appendChild(newPagination);
                        }
                    }
                } else if (currentPagination) {
                    // Если пагинация была, но теперь не нужна – удаляем
                    currentPagination.remove();
                }
            };
            
            const performSearch = () => {
                // Получаем текущее значение из input
                const currentSearchInput = document.getElementById('global-search');
                const searchValue = currentSearchInput ? currentSearchInput.value.trim() : '';
                
                // Сбрасываем страницу на первую при поиске
                const pageInput = searchForm.querySelector('input[name="page"]');
                if (pageInput) {
                    pageInput.value = '1';
                }
                
                // Убеждаемся, что input является частью формы и имеет правильный name
                if (currentSearchInput && currentSearchInput.form === searchForm) {
                    if (!currentSearchInput.getAttribute('name')) {
                        currentSearchInput.setAttribute('name', 'q');
                    }
                }
                
                // Формируем URL с параметрами поиска - используем прямое значение
                const params = new URLSearchParams();
                if (searchValue) {
                    params.set('q', searchValue);
                }
                params.set('page', '1');
                
                const url = `/users?${params.toString()}`;
                
                console.log('[VeilBot][users] Performing search:', { searchValue, url });

                // Выполняем запрос через fetch, чтобы обновить таблицу без перезагрузки
                fetch(url, {
                    method: 'GET',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'text/html',
                    },
                })
                    .then((response) => applySearchResponse(response))
                    .catch((error) => {
                        // В случае ошибки откатываемся к обычному переходу по ссылке
                        console.error('[VeilBot][users] live search failed, falling back to full reload', error);
                        window.location.href = url;
            });
        };
            
            const handleSearchInput = (event) => {
                // Останавливаем всплытие, чтобы клиентский поиск не сработал
                event.stopImmediatePropagation();
                event.stopPropagation();
                
                const searchValue = event.target.value;
                
                // Обновляем URL без перезагрузки
                const url = new URL(window.location.href);
                if (searchValue && searchValue.trim()) {
                    url.searchParams.set('q', searchValue.trim());
                } else {
                    url.searchParams.delete('q');
                }
                url.searchParams.set('page', '1');
                window.history.pushState({}, '', url.toString());
                
                // Debounce: ждем 500ms после последнего ввода
                if (searchTimeout) {
                    clearTimeout(searchTimeout);
                }
                searchTimeout = setTimeout(performSearch, 500);
            };
            
            const handleSearchKeydown = (event) => {
                // При нажатии Enter сразу отправляем
                if (event.key === 'Enter') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    if (searchTimeout) {
                        clearTimeout(searchTimeout);
                    }
                    performSearch();
                }
            };
            
            const handleSearchSubmit = (event) => {
                event.preventDefault();
                event.stopImmediatePropagation();
                if (searchTimeout) {
                    clearTimeout(searchTimeout);
                }
                performSearch();
            };
            
            // Удаляем все существующие обработчики, если они есть, и добавляем новые
            // Клонируем input чтобы удалить старые обработчики от common.js
            // Но сначала сохраняем все атрибуты и значение
            const currentValue = searchInput.value;
            const currentName = searchInput.getAttribute('name');
            const currentPlaceholder = searchInput.getAttribute('placeholder');
            const currentClass = searchInput.getAttribute('class');
            
            const newInput = searchInput.cloneNode(true);
            searchInput.parentNode.replaceChild(newInput, searchInput);
            const freshSearchInput = document.getElementById('global-search');
            
            if (!freshSearchInput) {
                console.error('[VeilBot][users] Failed to find input after cloning');
                return;
            }
            
            // Восстанавливаем все атрибуты и значение
            freshSearchInput.value = currentValue;
            if (currentName) {
                freshSearchInput.setAttribute('name', currentName);
            }
            if (currentPlaceholder) {
                freshSearchInput.setAttribute('placeholder', currentPlaceholder);
            }
            if (currentClass) {
                freshSearchInput.setAttribute('class', currentClass);
            }
            
            // Убеждаемся, что атрибуты установлены (это важно для common.js)
            freshSearchInput.setAttribute('data-server-search', '1');
            freshSearchInput.setAttribute('data-auto-search', '1');
            
            // Добавляем обработчики с capture: true чтобы перехватить событие до других обработчиков
            // Используем обертку для input события, чтобы убедиться, что оно срабатывает
            const inputHandler = (e) => {
                console.log('[VeilBot][users] Input event triggered', e.target.value);
                handleSearchInput(e);
            };
            
            freshSearchInput.addEventListener('input', inputHandler, { capture: true, passive: false });
            freshSearchInput.addEventListener('keydown', handleSearchKeydown, { capture: true, passive: false });
            searchForm.addEventListener('submit', handleSearchSubmit, { capture: true, passive: false });
            
            // Проверяем, что обработчик действительно добавлен
            console.log('[VeilBot][users] Event listeners added, input element:', freshSearchInput);
            
            // Обновляем handleResetSearch чтобы использовать freshSearchInput
            const handleResetSearchUpdated = (event) => {
                event.preventDefault();
                event.stopImmediatePropagation();
                
                // Очищаем поле ввода
                freshSearchInput.value = '';
                
                // Обновляем URL без перезагрузки
                const url = new URL(window.location.href);
                url.searchParams.delete('q');
                url.searchParams.set('page', '1');
                window.history.pushState({}, '', url.toString());
                
                // Выполняем поиск с пустым запросом
                if (searchTimeout) {
                    clearTimeout(searchTimeout);
                }
                // Обновляем значение в форме перед поиском
                const pageInput = searchForm.querySelector('input[name="page"]');
                if (pageInput) {
                    pageInput.value = '1';
                }
                performSearch();
            };
            
            if (resetSearchBtn) {
                resetSearchBtn.addEventListener('click', handleResetSearchUpdated, { capture: true, passive: false });
            }
            
            console.log('[VeilBot][users] Live search initialized on element:', freshSearchInput);
        } else {
            console.warn('[VeilBot][users] Search form or input not found');
        }
    }, 300); // Увеличена задержка до 300ms чтобы common.js успел проверить атрибуты

    // Обработчик VIP чекбоксов
    initVipHandlers();
};

const initVipHandlers = () => {
    // Удаляем старые обработчики, если они есть - клонируем checkbox чтобы удалить старые обработчики
    document.querySelectorAll('.vip-checkbox').forEach(checkbox => {
        const newCheckbox = checkbox.cloneNode(true);
        checkbox.parentNode.replaceChild(newCheckbox, checkbox);
    });

    // Добавляем новые обработчики VIP чекбоксов
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

// Экспортируем функцию для переинициализации после обновления таблицы
if (typeof window !== 'undefined') {
    window.initUsersPage = initUsersPage;
}

export {};

