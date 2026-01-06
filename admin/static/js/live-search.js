/**
 * Универсальная функция для live-поиска на всех страницах
 * Использование: initLiveSearch({ pageUrl: '/users', tableSelector: '#users-table', statsSelector: '.stats-grid' })
 */
(function() {
    'use strict';

    window.initLiveSearch = function(config) {
        const {
            pageUrl = window.location.pathname,
            tableSelector = 'table',
            statsSelector = '.stats-grid',
            paginationSelector = '.pagination',
            searchInputId = 'global-search',
            searchFormId = 'search-form',
            resetBtnId = 'reset-search-btn',
            debounceMs = 500
        } = config;

        // Инициализация с задержкой, чтобы common.js успел загрузиться
        setTimeout(() => {
            const searchForm = document.getElementById(searchFormId);
            const searchInput = document.getElementById(searchInputId);
            const resetSearchBtn = document.getElementById(resetBtnId);

            if (!searchForm || !searchInput) {
                console.warn('[VeilBot][live-search] Search form or input not found');
                return;
            }

            // Убеждаемся, что клиентский поиск не активен
            searchInput.setAttribute('data-server-search', '1');
            searchInput.setAttribute('data-auto-search', '1');

            let searchTimeout = null;

            const applySearchResponse = async (response) => {
                if (!response.ok) {
                    throw new Error(`Search request failed with status ${response.status}`);
                }
                const html = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');

                const newStats = doc.querySelector(statsSelector);
                const newTableBody = doc.querySelector(`${tableSelector} tbody`);
                const newPagination = doc.querySelector(paginationSelector);

                const currentStats = document.querySelector(statsSelector);
                const currentTableBody = document.querySelector(`${tableSelector} tbody`);
                const currentPagination = document.querySelector(paginationSelector);

                if (newStats && currentStats) {
                    currentStats.replaceWith(newStats);
                }
                if (newTableBody && currentTableBody) {
                    currentTableBody.replaceWith(newTableBody);
                    // Вызываем событие для переинициализации обработчиков на странице
                    const event = new CustomEvent('tableUpdated', { 
                        detail: { tableSelector, newTableBody } 
                    });
                    document.dispatchEvent(event);
                }
                if (newPagination) {
                    if (currentPagination) {
                        currentPagination.replaceWith(newPagination);
                    } else {
                        // Если пагинации не было, но теперь есть – добавляем после таблицы
                        const tableWrapper = document.querySelector('.table-scroll') || 
                                           document.querySelector('.table-container') ||
                                           document.querySelector(tableSelector)?.closest('.card');
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
                // Сбрасываем страницу на первую при поиске
                const pageInput = searchForm.querySelector('input[name="page"]');
                if (pageInput) {
                    pageInput.value = '1';
                }
                // Формируем URL с параметрами поиска
                const formData = new FormData(searchForm);
                const params = new URLSearchParams(formData);
                const url = `${pageUrl}?${params.toString()}`;

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
                        console.error('[VeilBot][live-search] Search failed, falling back to full reload', error);
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

                // Debounce: ждем после последнего ввода
                if (searchTimeout) {
                    clearTimeout(searchTimeout);
                }
                searchTimeout = setTimeout(performSearch, debounceMs);
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

            // Клонируем input чтобы удалить старые обработчики от common.js
            const newInput = searchInput.cloneNode(true);
            const currentValue = searchInput.value;
            searchInput.parentNode.replaceChild(newInput, searchInput);
            const freshSearchInput = document.getElementById(searchInputId);

            // Восстанавливаем значение
            freshSearchInput.value = currentValue;

            // Убеждаемся, что атрибуты установлены
            freshSearchInput.setAttribute('data-server-search', '1');
            freshSearchInput.setAttribute('data-auto-search', '1');

            // Добавляем обработчики
            freshSearchInput.addEventListener('input', handleSearchInput, { capture: true, passive: false });
            freshSearchInput.addEventListener('keydown', handleSearchKeydown, { capture: true, passive: false });
            searchForm.addEventListener('submit', handleSearchSubmit, { capture: true, passive: false });

            // Обработчик для кнопки "Сбросить"
            const handleResetSearch = (event) => {
                event.preventDefault();
                event.stopImmediatePropagation();

                // Очищаем поле ввода
                freshSearchInput.value = '';

                // Обновляем URL без перезагрузки, сохраняя другие параметры (например, vip_filter)
                const url = new URL(window.location.href);
                url.searchParams.delete('q');
                url.searchParams.set('page', '1');
                window.history.pushState({}, '', url.toString());

                // Выполняем поиск с пустым запросом
                if (searchTimeout) {
                    clearTimeout(searchTimeout);
                }
                const pageInput = searchForm.querySelector('input[name="page"]');
                if (pageInput) {
                    pageInput.value = '1';
                }
                performSearch();
            };

            if (resetSearchBtn) {
                resetSearchBtn.addEventListener('click', handleResetSearch, { capture: true, passive: false });
            }

            console.log('[VeilBot][live-search] Initialized for', pageUrl);
        }, 100);
    };
})();

