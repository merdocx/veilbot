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

    const state = {
        modal: null,
        editForm: null,
        expiryInput: null,
        trafficLimitInput: null,
        currentKeyId: null,
    };

    const closeModal = () => {
        if (!state.modal) {
            return;
        }
        state.modal.classList.remove('vb-modal--open');
        state.modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('vb-modal-open');
        const backdrop = state.modal.querySelector('.vb-modal__backdrop');
        if (backdrop) {
            backdrop.removeAttribute('aria-hidden');
        }
        if (state.editForm) {
            state.editForm.reset();
        }
    };

    const openModal = (trigger) => {
        if (!state.modal || !state.editForm || !state.expiryInput) {
            return;
        }
        const keyId = trigger.dataset.keyId;
        if (!keyId) {
            return;
        }
        const expiryValue = trigger.dataset.expiry || '';
        const limitValue = trigger.dataset.limit ?? '';

        state.currentKeyId = keyId;
        state.editForm.dataset.keyId = keyId;
        state.editForm.action = `/keys/edit/${keyId}`;
        state.expiryInput.value = expiryValue;
        if (state.trafficLimitInput) {
            state.trafficLimitInput.value = limitValue !== undefined ? limitValue : '';
        }

        state.modal.classList.add('vb-modal--open');
        state.modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('vb-modal-open');

        const firstFocusable = state.modal.querySelector('[data-modal-focus], input, button, select, textarea');
        if (firstFocusable && typeof firstFocusable.focus === 'function') {
            firstFocusable.focus();
        }
    };

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

    const resetFormLoadingState = (form) => {
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = false;
        }
        form.classList.remove('is-loading');
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

        const editButton = row.querySelector('[data-action="edit-key"]');
        if (editButton) {
            editButton.dataset.expiry = key.expiry_iso || '';
            const limitMb = key.traffic && key.traffic.limit_mb != null
                ? String(key.traffic.limit_mb)
                : '';
            editButton.dataset.limit = limitMb;
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

    const handleEditSubmit = async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
        }
        form.classList.add('is-loading');

        const formData = new FormData(form);

        try {
            const response = await fetch(form.action, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: formData,
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Не удалось обновить ключ');
            }

            if (data.key) {
                applyKeyUpdate(data.key);
            }
            if (data.stats) {
                updateStats(data.stats);
            }

            notify(data.message || 'Параметры ключа обновлены', 'success');
            closeModal();
        } catch (error) {
            handleError(error, 'Обновление ключа');
        } finally {
            resetFormLoadingState(form);
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

        if (action === 'edit-key') {
            event.preventDefault();
            openModal(trigger);
            return;
        }

        if (action === 'delete-key') {
            event.preventDefault();
            handleDeleteKey(trigger);
        }
    };

    const bindModalHandlers = () => {
        if (!state.modal) {
            return;
        }
        const backdrop = state.modal.querySelector('.vb-modal__backdrop');
        if (backdrop) {
            backdrop.addEventListener('click', closeModal);
        }

        state.modal.querySelectorAll('[data-modal-close]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.preventDefault();
                closeModal();
            });
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && state.modal && state.modal.classList.contains('vb-modal--open')) {
                closeModal();
            }
        });
    };

    const init = () => {
        state.modal = document.getElementById('edit-key-modal');
        state.editForm = document.getElementById('editForm');
        state.expiryInput = document.getElementById('new_expiry');
        state.trafficLimitInput = document.getElementById('traffic_limit_mb');

        if (state.modal) {
            state.modal.setAttribute('aria-hidden', 'true');
            bindModalHandlers();
        }

        if (state.editForm) {
            state.editForm.addEventListener('submit', handleEditSubmit);
        }

        const table = document.getElementById('keys-table');
        if (table) {
            table.addEventListener('click', handleTableClick);
        }

        // Серверный поиск вместо клиентского
        const searchForm = document.getElementById('search-form');
        const searchInput = document.getElementById('global-search');
        const resetSearchBtn = document.getElementById('reset-search-btn');
        
        if (searchForm && searchInput) {
            // Убеждаемся, что клиентский поиск не активен для этого элемента
            searchInput.setAttribute('data-server-search', '1');
            searchInput.setAttribute('data-auto-search', '1');
            
            let searchTimeout = null;
            
            const performSearch = () => {
                // Сбрасываем страницу на первую при поиске
                const pageInput = searchForm.querySelector('input[name="page"]');
                if (pageInput) {
                    pageInput.value = '1';
                }
                // Отправляем форму на сервер
                searchForm.submit();
            };
            
            const handleSearchInput = (event) => {
                // Останавливаем всплытие, чтобы клиентский поиск не сработал
                event.stopImmediatePropagation();
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
            
            const handleResetSearch = (event) => {
                event.preventDefault();
                event.stopImmediatePropagation();
                if (searchInput.value) {
                    searchInput.value = '';
                    // При сбросе поиска убираем параметр q из URL
                    const url = new URL(window.location.href);
                    url.searchParams.delete('q');
                    url.searchParams.set('page', '1');
                    window.location.href = url.toString();
                }
            };
            
            // Удаляем все существующие обработчики, если они есть
            const newInput = searchInput.cloneNode(true);
            searchInput.parentNode.replaceChild(newInput, searchInput);
            const freshSearchInput = document.getElementById('global-search');
            
            // Добавляем обработчики с capture фазой, чтобы они сработали первыми
            freshSearchInput.addEventListener('input', handleSearchInput, { capture: true, passive: false });
            freshSearchInput.addEventListener('keydown', handleSearchKeydown, { capture: true, passive: false });
            searchForm.addEventListener('submit', handleSearchSubmit, { capture: true, passive: false });
            
            if (resetSearchBtn) {
                resetSearchBtn.addEventListener('click', handleResetSearch, { capture: true, passive: false });
            }
        }

        updateProgressBars();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();