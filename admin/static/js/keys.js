(() => {
    const common = window.VeilBotCommon || {};
    const notify = common.showNotification
        || ((message, type) => {
            if (type === 'error') {
                console.error(message);
            }
            window.alert(message);
        });
    const handleError = common.handleError
        || ((error, context) => {
            console.error('[VeilBot][keys] error in', context, error);
            notify('Произошла ошибка. Проверьте консоль.', 'error');
        });
    const updateProgressBars = common.updateProgressBars || (() => {});


    const copyToClipboard = async (value) => {
        const text = value || '';
        if (!text) {
            notify('Ключ отсутствует', 'warning');
            return false;
        }

        try {
            if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(text);
                notify('Ключ скопирован в буфер обмена', 'success');
                return true;
            }
        } catch (error) {
            handleError(error, 'Clipboard API');
        }

        try {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            textarea.style.pointerEvents = 'none';
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            const success = document.execCommand('copy');
            document.body.removeChild(textarea);
            if (success) {
                notify('Ключ скопирован в буфер обмена', 'success');
                return true;
            }
        } catch (error) {
            handleError(error, 'document.execCommand copy');
        }

        window.prompt('Скопируйте ключ вручную (Ctrl/Cmd + C):', text);
        return false;
    };

    const updateStats = (stats) => {
        if (!stats) {
            return;
        }
        Object.entries(stats).forEach(([key, value]) => {
            const target = document.querySelector(`.stat-card[data-stat="${key}"] [data-stat-value]`);
            if (target) {
                target.textContent = value;
            }
        });
    };

    const applyKeyUpdate = (key) => {
        if (!key) return;
        const row = document.querySelector(`.keys-table__row[data-key-id="${key.id}"]`);
        if (!row) return;

        row.dataset.status = key.status;
        row.dataset.expiryTs = key.expiry_at;
        row.dataset.expiryIso = key.expiry_iso || '';

        const expiryDisplay = row.querySelector('[data-field="expiry-display"]');
        if (expiryDisplay) {
            expiryDisplay.textContent = key.expiry_display;
        }

        const expiryRemaining = row.querySelector('[data-field="expiry-remaining"]');
        if (expiryRemaining) {
            expiryRemaining.textContent = key.expiry_remaining;
        }

        const expiryBar = row.querySelector('.keys-table__cell--expiry .progress-bar');
        if (expiryBar) {
            const percent = Math.round((key.lifetime_progress || 0) * 100);
            expiryBar.dataset.progress = Number.isFinite(percent) ? percent : 0;
        }

        const statusIcon = row.querySelector('[data-field="status-icon"]');
        if (statusIcon) {
            statusIcon.textContent = key.status_icon;
            statusIcon.setAttribute('title', key.status_label);
            statusIcon.classList.remove('status-icon--active', 'status-icon--expired');
            statusIcon.classList.add(key.status_class);
        }


        const trafficCell = row.querySelector('.traffic-cell');
        if (trafficCell && key.traffic) {
            trafficCell.dataset.trafficState = key.traffic.state || 'na';
            const display = trafficCell.querySelector('[data-field="traffic-display"]');
            if (display) {
                display.textContent = key.traffic.display;
            }
            const limit = trafficCell.querySelector('[data-field="traffic-limit"]');
            if (limit) {
                if (key.traffic.limit_display && key.traffic.limit_display !== '—') {
                    limit.textContent = `Лимит: ${key.traffic.limit_display}`;
                    limit.classList.remove('text-muted');
                } else {
                    limit.textContent = 'Лимит не задан';
                    limit.classList.add('text-muted');
                }
            }
            const warning = trafficCell.querySelector('[data-field="traffic-warning"]');
            const isOverLimit = Boolean(key.traffic.over_limit);
            trafficCell.dataset.overLimit = isOverLimit ? '1' : '0';
            trafficCell.dataset.overLimitDeadline = key.traffic.over_limit_deadline || '';
            trafficCell.classList.toggle('traffic-cell--over-limit', isOverLimit);
            if (warning) {
                if (isOverLimit) {
                    const deadlineText = key.traffic.over_limit_deadline_display || '';
                    warning.textContent = deadlineText
                        ? `Превышен лимит. ${deadlineText}`
                        : 'Превышен лимит. Ключ будет отключён без продления.';
                    warning.classList.remove('hidden');
                } else {
                    warning.textContent = '';
                    warning.classList.add('hidden');
                }
            }
            const trafficBar = trafficCell.querySelector('.progress-bar');
            if (trafficBar) {
                const percent = key.traffic.usage_percent != null
                    ? Math.round(key.traffic.usage_percent * 100)
                    : 0;
                trafficBar.dataset.progress = Number.isFinite(percent) ? percent : 0;
            }
        }

        if (key.access_url) {
            const copyButton = row.querySelector('[data-action="copy-key"]');
            if (copyButton) {
                copyButton.dataset.key = key.access_url;
            }
        }

        updateProgressBars(row);
    };

    const removeKeyRow = (keyId) => {
        const row = document.querySelector(`.keys-table__row[data-key-id="${keyId}"]`);
        if (row && row.parentElement) {
            row.parentElement.removeChild(row);
        }
    };


    const handleDeleteKey = async (trigger) => {
        const keyId = trigger.dataset.keyId;
        if (!keyId) {
            return;
        }

        if (!window.confirm(`Удалить ключ ${keyId}?`)) {
            return;
        }

        const fallbackUrl = trigger.getAttribute('href');

        try {
            const response = await fetch(`/api/keys/${keyId}`, {
                method: 'DELETE',
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Не удалось удалить ключ');
            }

            removeKeyRow(keyId);
            if (data.stats) {
                updateStats(data.stats);
            }
            notify(data.message || 'Ключ удалён', 'success');
        } catch (error) {
            handleError(error, 'Удаление ключа');
            if (fallbackUrl) {
                window.location.href = fallbackUrl;
            }
        }
    };

    const handleTableClick = (event) => {
        const trigger = event.target.closest('[data-action]');
        if (!trigger) {
            return;
        }

        const action = trigger.dataset.action;
        if (action === 'copy-key') {
            event.preventDefault();
            const result = copyToClipboard(trigger.dataset.key);
            if (result instanceof Promise) {
                result.catch(() => notify('Скопируйте ключ вручную (Ctrl/Cmd + C)', 'info', 5000));
            } else if (!result) {
                notify('Скопируйте ключ вручную (Ctrl/Cmd + C)', 'info', 5000);
            }
            return;
        }

        if (action === 'delete-key') {
            event.preventDefault();
            handleDeleteKey(trigger);
        }
    };

    const init = () => {

        const table = document.getElementById('keys-table');
        if (table) {
            table.addEventListener('click', handleTableClick);
        }

        // Серверный поиск вместо клиентского (live-поиск без перезагрузки страницы)
        // Инициализируем поиск с небольшой задержкой, чтобы common.js успел загрузиться
        setTimeout(() => {
            const searchForm = document.getElementById('search-form');
            const searchInput = document.getElementById('global-search');
            const resetSearchBtn = document.getElementById('reset-search-btn');
        
        if (searchForm && searchInput) {
            // Убеждаемся, что клиентский поиск не активен для этого элемента
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

                const newStats = doc.querySelector('.stats-grid');
                const newTableBody = doc.querySelector('#keys-table tbody');
                const newPagination = doc.querySelector('.pagination');

                const currentStats = document.querySelector('.stats-grid');
                const currentTableBody = document.querySelector('#keys-table tbody');
                const currentPagination = document.querySelector('.pagination');

                if (newStats && currentStats) {
                    currentStats.replaceWith(newStats);
                }
                if (newTableBody && currentTableBody) {
                    currentTableBody.replaceWith(newTableBody);
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

                // После замены DOM нужно заново навесить обработчики событий
                const updatedTable = document.getElementById('keys-table');
                if (updatedTable) {
                    updatedTable.removeEventListener('click', handleTableClick);
                    updatedTable.addEventListener('click', handleTableClick);
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
                const url = `/keys?${params.toString()}`;

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
                        console.error('[VeilBot][keys] live search failed, falling back to full reload', error);
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
            const newInput = searchInput.cloneNode(true);
            // Сохраняем значение перед клонированием
            const currentValue = searchInput.value;
            searchInput.parentNode.replaceChild(newInput, searchInput);
            const freshSearchInput = document.getElementById('global-search');
            
            // Восстанавливаем значение
            freshSearchInput.value = currentValue;
            
            // Убеждаемся, что атрибуты установлены
            freshSearchInput.setAttribute('data-server-search', '1');
            freshSearchInput.setAttribute('data-auto-search', '1');
            
            // Добавляем обработчики с capture: true чтобы перехватить событие до других обработчиков
            // Используем once: false чтобы обработчик работал постоянно
            freshSearchInput.addEventListener('input', (e) => {
                console.log('[VeilBot][keys] Input event triggered', e.target.value);
                handleSearchInput(e);
            }, { capture: true, passive: false });
            
            freshSearchInput.addEventListener('keydown', handleSearchKeydown, { capture: true, passive: false });
            searchForm.addEventListener('submit', handleSearchSubmit, { capture: true, passive: false });
            
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
            
            // Для отладки: проверяем что обработчик работает
            console.log('[VeilBot][keys] Live search initialized on element:', freshSearchInput);
        }
        }, 100); // Задержка 100ms чтобы common.js успел инициализироваться

        updateProgressBars();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();