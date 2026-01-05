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
            console.error('[VeilBot][subscriptions] error in', context, error);
            notify('Произошла ошибка. Проверьте консоль.', 'error');
        });
    const updateProgressBars = common.updateProgressBars || (() => {});

    const state = {
        modal: null,
        editForm: null,
        expiryInput: null,
        trafficLimitInput: null,
        currentSubscriptionId: null,
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
            console.warn('[VeilBot][subscriptions] Modal or form elements not found');
            return;
        }
        const subscriptionId = trigger.dataset.subscriptionId;
        if (!subscriptionId) {
            console.warn('[VeilBot][subscriptions] No subscription ID found');
            return;
        }
        const expiryValue = trigger.dataset.expiry || '';
        const limitValue = trigger.dataset.trafficLimit ?? '';
        
        console.log(`[VeilBot][subscriptions] Opening modal for subscription ${subscriptionId}`);
        console.log(`[VeilBot][subscriptions] Expiry value: ${expiryValue}, Limit value: ${limitValue}`);

        state.currentSubscriptionId = subscriptionId;
        state.editForm.dataset.subscriptionId = subscriptionId;
        state.editForm.action = `/subscriptions/edit/${subscriptionId}`;
        state.expiryInput.value = expiryValue;
        if (state.trafficLimitInput) {
            state.trafficLimitInput.value = limitValue !== undefined && limitValue !== '' ? limitValue : '';
            console.log(`[VeilBot][subscriptions] Set trafficLimitInput value to: ${state.trafficLimitInput.value}`);
        } else {
            console.warn('[VeilBot][subscriptions] trafficLimitInput not found');
        }

        state.modal.classList.add('vb-modal--open');
        state.modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('vb-modal-open');

        const firstFocusable = state.modal.querySelector('[data-modal-focus], input, button, select, textarea');
        if (firstFocusable && typeof firstFocusable.focus === 'function') {
            firstFocusable.focus();
        }
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

    const applySubscriptionUpdate = (subscription) => {
        console.log('[VeilBot][subscriptions] applySubscriptionUpdate called with:', subscription);
        if (!subscription) {
            console.warn('[VeilBot][subscriptions] No subscription data provided');
            return;
        }
        const row = document.querySelector(`.subscriptions-table__row[data-subscription-id="${subscription.id}"]`);
        if (!row) {
            console.warn(`[VeilBot][subscriptions] Row not found for subscription ${subscription.id}`);
            return;
        }
        console.log('[VeilBot][subscriptions] Found row, updating...');

        row.dataset.status = subscription.status || '';
        row.dataset.expiryTs = subscription.expires_at_ts || '';
        row.dataset.expiryIso = subscription.expires_at_iso || '';

        const expiryDisplay = row.querySelector('[data-field="expiry-display"]');
        if (expiryDisplay) {
            expiryDisplay.textContent = subscription.expires_at || '';
        }

        const expiryRemaining = row.querySelector('[data-field="expiry-remaining"]');
        if (expiryRemaining) {
            expiryRemaining.textContent = subscription.expiry_remaining || '';
        }

        const expiryBar = row.querySelector('.subscriptions-table__cell--expiry .progress-bar');
        if (expiryBar && subscription.lifetime_progress !== undefined) {
            const percent = Math.round((subscription.lifetime_progress || 0) * 100);
            expiryBar.dataset.progress = Number.isFinite(percent) ? percent : 0;
        }

        const statusIcon = row.querySelector('[data-field="status-icon"]');
        if (statusIcon) {
            statusIcon.textContent = subscription.status === "Активна" ? "check_circle" : "cancel";
            statusIcon.setAttribute('title', subscription.status || '');
            statusIcon.classList.remove('status-active', 'status-expired');
            statusIcon.classList.add(subscription.status_class || '');
        }

        const editButton = row.querySelector('[data-action="edit-subscription"]');
        if (editButton) {
            editButton.dataset.expiry = subscription.expires_at_iso || '';
            // Обновляем значение лимита трафика в data-атрибуте (используем traffic.limit_mb как в keys.js)
            const limitMb = subscription.traffic && subscription.traffic.limit_mb != null
                ? String(subscription.traffic.limit_mb)
                : '';
            console.log(`[VeilBot][subscriptions] Updating edit button traffic_limit_mb from traffic.limit_mb: ${limitMb}`);
            editButton.dataset.trafficLimit = limitMb;
        }

        // Обновляем информацию о трафике
        if (subscription.traffic) {
            const trafficDisplay = row.querySelector('[data-field="traffic-display"]');
            if (trafficDisplay) {
                if (subscription.traffic && subscription.traffic.display) {
                    trafficDisplay.textContent = subscription.traffic.display;
                } else {
                    trafficDisplay.textContent = '—';
                }
            }

            const trafficLimit = row.querySelector('[data-field="traffic-limit"]');
            if (trafficLimit) {
                if (subscription.traffic && subscription.traffic.limit_display && subscription.traffic.limit_display !== '—') {
                    trafficLimit.textContent = `Лимит: ${subscription.traffic.limit_display}`;
                    trafficLimit.classList.remove('text-muted');
                } else {
                    trafficLimit.textContent = 'Лимит не задан';
                    trafficLimit.classList.add('text-muted');
                }
            }

            const trafficCell = row.querySelector('.subscriptions-table__cell--traffic');
            if (trafficCell) {
                if (subscription.traffic.over_limit) {
                    trafficCell.classList.add('traffic-cell--over-limit');
                } else {
                    trafficCell.classList.remove('traffic-cell--over-limit');
                }
            }

            const trafficProgressBar = row.querySelector('.subscriptions-table__cell--traffic .progress-bar');
            if (trafficProgressBar && subscription.traffic.usage_percent !== undefined && subscription.traffic.usage_percent !== null) {
                const percent = Math.round((subscription.traffic.usage_percent || 0) * 100);
                trafficProgressBar.dataset.progress = Number.isFinite(percent) ? percent : 0;
            }
        }

        updateProgressBars(row);
    };

    const removeSubscriptionRow = (subscriptionId) => {
        const row = document.querySelector(`.subscriptions-table__row[data-subscription-id="${subscriptionId}"]`);
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
        
        
        // Обрабатываем traffic_limit_mb: явно обрабатываем значение 0
        // Важно: если пользователь вводит 0, нужно явно отправить "0", а не пустую строку
        const trafficLimitInput = form.querySelector('#traffic_limit_mb');
        if (trafficLimitInput) {
            const trafficLimitValue = trafficLimitInput.value.trim();
            // Если поле пустое, отправляем пустую строку (будет обработано как 0 на сервере)
            // Если поле содержит 0, явно отправляем "0"
            if (trafficLimitValue === '' || trafficLimitValue === '0') {
                formData.set('traffic_limit_mb', trafficLimitValue === '0' ? '0' : '');
            }
            // Если значение не пустое и не 0, оно уже в FormData
        }
        

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
            console.log('[VeilBot][subscriptions] Response status:', response.status);
            console.log('[VeilBot][subscriptions] Response data:', JSON.stringify(data, null, 2));
            
            if (!response.ok) {
                console.error('[VeilBot][subscriptions] Error response:', data);
                throw new Error(data.error || 'Не удалось обновить подписку');
            }

            if (data.subscription) {
                console.log('[VeilBot][subscriptions] Updating subscription with data:', data.subscription);
                console.log('[VeilBot][subscriptions] traffic_limit_mb in response:', data.subscription.traffic_limit_mb);
                applySubscriptionUpdate(data.subscription);
            } else {
                console.warn('[VeilBot][subscriptions] No subscription data in response');
            }
            if (data.stats) {
                updateStats(data.stats);
            }

            notify(data.message || 'Параметры подписки обновлены', 'success');
            closeModal();
        } catch (error) {
            console.error('[VeilBot][subscriptions] Error:', error);
            handleError(error, 'Обновление подписки');
        } finally {
            resetFormLoadingState(form);
        }
    };


    const handleDeleteSubscription = async (trigger) => {
        const subscriptionId = trigger.dataset.subscriptionId;
        if (!subscriptionId) {
            return;
        }

        if (!window.confirm(`Удалить подписку ${subscriptionId}? Все связанные ключи также будут удалены.`)) {
            return;
        }

        const fallbackUrl = trigger.getAttribute('href');

        try {
            const response = await fetch(`/api/subscriptions/${subscriptionId}`, {
                method: 'DELETE',
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Не удалось удалить подписку');
            }

            removeSubscriptionRow(subscriptionId);
            if (data.stats) {
                updateStats(data.stats);
            }
            notify(data.message || 'Подписка удалена', 'success');
        } catch (error) {
            handleError(error, 'Удаление подписки');
            if (fallbackUrl) {
                window.location.href = fallbackUrl;
            }
        }
    };

    const openSyncKeysModal = () => {
        const syncModal = document.getElementById('sync-keys-modal');
        if (!syncModal) {
            console.warn('[VeilBot][subscriptions] Sync keys modal not found');
            return;
        }
        syncModal.classList.add('vb-modal--open');
        syncModal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('vb-modal-open');
        
        const firstFocusable = syncModal.querySelector('[data-modal-focus], input, button, select, textarea');
        if (firstFocusable && typeof firstFocusable.focus === 'function') {
            firstFocusable.focus();
        }
    };

    const closeSyncKeysModal = () => {
        const syncModal = document.getElementById('sync-keys-modal');
        if (!syncModal) {
            return;
        }
        syncModal.classList.remove('vb-modal--open');
        syncModal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('vb-modal-open');
        const syncForm = document.getElementById('syncKeysForm');
        if (syncForm) {
            syncForm.reset();
            // Восстанавливаем значения по умолчанию
            syncForm.querySelector('input[name="dry_run"][value="true"]').checked = true;
            syncForm.querySelector('input[name="server_scope"][value="all"]').checked = true;
            syncForm.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
            document.getElementById('server-select-field').style.display = 'none';
        }
    };

    const handleSyncKeys = async (syncParams) => {
        const syncButton = document.getElementById('sync-keys-btn');
        if (syncButton) {
            syncButton.disabled = true;
            const originalText = syncButton.innerHTML;
            syncButton.innerHTML = '<span class="material-icons icon-small">hourglass_empty</span> Синхронизация...';
            
            try {
                // Создаем AbortController для таймаута (10 минут)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 10 * 60 * 1000);
                
                const response = await fetch('/subscriptions/sync-keys', {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify(syncParams),
                    signal: controller.signal,
                });
                
                clearTimeout(timeoutId);

                const responseClone = response.clone();
                let data;
                try {
                    data = await response.json();
                } catch (jsonError) {
                    let textSnippet = '';
                    try {
                        const text = await responseClone.text();
                        textSnippet = text.substring(0, 200);
                    } catch (textError) {
                        textSnippet = `Failed to read response body: ${textError}`;
                    }
                    console.error('[VeilBot][subscriptions] Failed to parse JSON response:', textSnippet);
                    throw new Error(`Ошибка сервера (${response.status}): ${textSnippet}`);
                }

                if (!response.ok || !data.success) {
                    const errorMessage = data.error || data.message || 'Ошибка синхронизации';
                    console.error('[VeilBot][subscriptions] Sync error:', errorMessage);
                    throw new Error(errorMessage);
                }

                const stats = data.stats || {};
                const totalKeys = stats.total_keys || 0;
                const updated = stats.updated || 0;
                const unchanged = stats.unchanged || 0;
                const failed = stats.failed || 0;
                const serversProcessed = stats.servers_processed || 0;
                const missingPairs = stats.missing_pairs_total || 0;
                const missingCreated = stats.missing_keys_created || 0;
                const missingFailed = stats.missing_keys_failed || 0;
                const missingServers = stats.missing_keys_servers || 0;
                const errorDetails = stats.error_details || [];
                
                // Формируем детальное сообщение об успешной синхронизации
                let message = '✅ Синхронизация ключей завершена успешно! ';
                message += `Обработано ключей: ${totalKeys}, обновлено: ${updated}, без изменений: ${unchanged}`;
                if (failed > 0) {
                    message += `, ошибок: ${failed}`;
                }
                message += `. Серверов: ${serversProcessed}`;
                if (missingPairs > 0) {
                    message += `. Недостающих ключей найдено: ${missingPairs}, создано: ${missingCreated}`;
                    if (missingFailed > 0) {
                        message += `, ошибок при создании: ${missingFailed}`;
                    }
                    if (missingServers > 0) {
                        message += `. Серверов с недостающими ключами: ${missingServers}`;
                    }
                }
                
                // Добавляем детали ошибок, если они есть
                if (failed > 0 && errorDetails.length > 0) {
                    message += '\n\n❌ Детали ошибок:\n';
                    errorDetails.slice(0, 10).forEach((error, index) => {
                        const keyInfo = error.key_id ? `Ключ #${error.key_id}` : 'Неизвестный ключ';
                        const uuidInfo = error.v2ray_uuid ? ` (UUID: ${error.v2ray_uuid.substring(0, 8)}...)` : '';
                        const serverInfo = error.server_name ? ` [${error.server_name}]` : '';
                        message += `${index + 1}. ${keyInfo}${uuidInfo}${serverInfo}: ${error.error}\n`;
                    });
                    if (errorDetails.length > 10) {
                        message += `... и еще ${errorDetails.length - 10} ошибок`;
                    }
                }
                
                // Показываем уведомление с увеличенным временем отображения
                const notificationTime = failed > 0 ? 15000 : 5000; // Больше времени, если есть ошибки
                notify(message, failed > 0 ? 'warning' : 'success', notificationTime);
                
                // Восстанавливаем кнопку с индикацией успеха
                syncButton.innerHTML = '<span class="material-icons icon-small">check_circle</span> Синхронизировано';
                syncButton.classList.add('btn-success');
                
                // Обновляем страницу через 3 секунды, чтобы пользователь успел увидеть уведомление
                setTimeout(() => {
                    window.location.reload();
                }, 3000);
            } catch (error) {
                console.error('[VeilBot][subscriptions] Sync keys error:', error);
                let errorMessage = 'Неизвестная ошибка';
                
                if (error.name === 'AbortError') {
                    errorMessage = 'Синхронизация прервана по таймауту (более 10 минут). Процесс может продолжаться на сервере.';
                } else if (error instanceof TypeError && error.message.includes('fetch')) {
                    errorMessage = 'Ошибка сети при синхронизации. Проверьте подключение к интернету.';
                } else if (error instanceof Error) {
                    errorMessage = error.message;
                } else {
                    errorMessage = String(error);
                }
                
                notify(`Ошибка синхронизации: ${errorMessage}`, 'error', 8000);
                syncButton.innerHTML = originalText;
                syncButton.disabled = false;
                syncButton.classList.remove('btn-success');
            }
        }
    };

    const handleSyncKeysFormSubmit = async (e) => {
        e.preventDefault();
        
        const form = e.target;
        const formData = new FormData(form);
        
        // Собираем параметры
        const syncParams = {
            dry_run: formData.get('dry_run') === 'true',
            server_scope: formData.get('server_scope'),
            server_id: formData.get('server_scope') === 'single' ? parseInt(formData.get('server_id')) : null,
            create_missing: formData.has('create_missing'),
            delete_orphaned_on_servers: formData.has('delete_orphaned_on_servers'),
            delete_inactive_server_keys: formData.has('delete_inactive_server_keys'),
            sync_configs: formData.has('sync_configs'),
            include_v2ray: formData.has('include_v2ray'),
            include_outline: formData.has('include_outline'),
        };
        
        // Валидация: хотя бы один протокол должен быть выбран
        if (!syncParams.include_v2ray && !syncParams.include_outline) {
            const protocolHint = document.getElementById('protocol-hint');
            if (protocolHint) {
                protocolHint.style.display = 'block';
            }
            notify('Необходимо выбрать хотя бы один протокол (V2Ray или Outline)', 'error', 5000);
            return;
        }
        
        // Валидация: если выбран один сервер, должен быть указан server_id
        if (syncParams.server_scope === 'single' && !syncParams.server_id) {
            notify('Необходимо выбрать сервер при выборе "Один сервер"', 'error', 5000);
            return;
        }
        
        // Закрываем модальное окно
        closeSyncKeysModal();
        
        // Запускаем синхронизацию
        await handleSyncKeys(syncParams);
    };

    const handleTableClick = (event) => {
        const trigger = event.target.closest('[data-action]');
        if (!trigger) {
            return;
        }

        const action = trigger.dataset.action;
        if (action === 'edit-subscription') {
            event.preventDefault();
            openModal(trigger);
            return;
        }

        if (action === 'delete-subscription') {
            event.preventDefault();
            handleDeleteSubscription(trigger);
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
        state.modal = document.getElementById('edit-subscription-modal');
        state.editForm = document.getElementById('editSubscriptionForm');
        state.expiryInput = document.getElementById('new_expiry');
        state.trafficLimitInput = document.getElementById('traffic_limit_mb');

        if (state.modal) {
            state.modal.setAttribute('aria-hidden', 'true');
            bindModalHandlers();
        }

        if (state.editForm) {
            state.editForm.addEventListener('submit', handleEditSubmit);
        }

        const table = document.getElementById('subscriptions-table');
        if (table) {
            table.addEventListener('click', handleTableClick);
        }

        const syncButton = document.getElementById('sync-keys-btn');
        if (syncButton) {
            syncButton.addEventListener('click', openSyncKeysModal);
        }
        
        // Обработчик формы синхронизации
        const syncKeysForm = document.getElementById('syncKeysForm');
        if (syncKeysForm) {
            syncKeysForm.addEventListener('submit', handleSyncKeysFormSubmit);
        }
        
        // Показываем/скрываем выбор сервера в зависимости от выбранной области
        const serverScopeRadios = document.querySelectorAll('input[name="server_scope"]');
        const serverSelectField = document.getElementById('server-select-field');
        const serverIdSelect = document.getElementById('server_id');
        
        serverScopeRadios.forEach(radio => {
            radio.addEventListener('change', () => {
                if (radio.value === 'single') {
                    if (serverSelectField) {
                        serverSelectField.style.display = 'block';
                    }
                    if (serverIdSelect) {
                        serverIdSelect.required = true;
                    }
                } else {
                    if (serverSelectField) {
                        serverSelectField.style.display = 'none';
                    }
                    if (serverIdSelect) {
                        serverIdSelect.required = false;
                        serverIdSelect.value = '';
                    }
                }
            });
        });
        
        // Скрываем подсказку об ошибке протоколов при изменении чекбоксов
        const protocolCheckboxes = document.querySelectorAll('input[name="include_v2ray"], input[name="include_outline"]');
        const protocolHint = document.getElementById('protocol-hint');
        protocolCheckboxes.forEach(checkbox => {
            checkbox.addEventListener('change', () => {
                if (protocolHint) {
                    protocolHint.style.display = 'none';
                }
            });
        });
        
        // Обработчик закрытия модального окна синхронизации
        const syncModal = document.getElementById('sync-keys-modal');
        if (syncModal) {
            const closeButtons = syncModal.querySelectorAll('[data-modal-close]');
            closeButtons.forEach(btn => {
                btn.addEventListener('click', closeSyncKeysModal);
            });
            
            // Закрытие по клику на backdrop
            const backdrop = syncModal.querySelector('.vb-modal__backdrop');
            if (backdrop) {
                backdrop.addEventListener('click', closeSyncKeysModal);
            }
        }

        // Серверный поиск вместо клиентского (live-поиск без перезагрузки страницы)
        // Инициализируем поиск с задержкой, чтобы common.js успел проверить атрибуты
        setTimeout(() => {
            const searchForm = document.getElementById('search-form');
            const searchInput = document.getElementById('global-search');
            const resetSearchBtn = document.getElementById('reset-search-btn');
        
            if (searchForm && searchInput) {
                // Убеждаемся, что клиентский поиск не активен для этого элемента
                // Устанавливаем атрибуты ДО клонирования, чтобы common.js их видел
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
                    const newTableBody = doc.querySelector('#subscriptions-table tbody');
                    const newPagination = doc.querySelector('.pagination');

                    const currentStats = document.querySelector('.stats-grid');
                    const currentTableBody = document.querySelector('#subscriptions-table tbody');
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
                            const tableWrapper = document.querySelector('.table-scroll');
                            if (tableWrapper && tableWrapper.parentElement) {
                                tableWrapper.parentElement.appendChild(newPagination);
                            }
                        }
                    } else if (currentPagination) {
                        currentPagination.remove();
                    }
                    
                    // Переинициализируем только обработчики после обновления таблицы (но не поиск, чтобы избежать циклов)
                    updateProgressBars();
                    const updatedTable = document.getElementById('subscriptions-table');
                    if (updatedTable) {
                        // Удаляем старый обработчик и добавляем новый
                        updatedTable.removeEventListener('click', handleTableClick);
                        updatedTable.addEventListener('click', handleTableClick);
                    }
                };
                
                const performSearch = () => {
                    const currentSearchInput = document.getElementById('global-search');
                    const searchValue = currentSearchInput ? currentSearchInput.value.trim() : '';
                    
                    const pageInput = searchForm.querySelector('input[name="page"]');
                    if (pageInput) {
                        pageInput.value = '1';
                    }
                    
                    if (currentSearchInput && currentSearchInput.form === searchForm) {
                        if (!currentSearchInput.getAttribute('name')) {
                            currentSearchInput.setAttribute('name', 'q');
                        }
                    }
                    
                    const params = new URLSearchParams();
                    if (searchValue) {
                        params.set('q', searchValue);
                    }
                    params.set('page', '1');
                    
                    const url = `/subscriptions?${params.toString()}`;

                    fetch(url, {
                        method: 'GET',
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'text/html',
                        },
                    })
                        .then((response) => applySearchResponse(response))
                        .catch((error) => {
                            console.error('[VeilBot][subscriptions] live search failed, falling back to full reload', error);
                            window.location.href = url;
                        });
                };
                
                const handleSearchInput = (event) => {
                    event.stopImmediatePropagation();
                    event.stopPropagation();
                    
                    const searchValue = event.target.value;
                    
                    const url = new URL(window.location.href);
                    if (searchValue && searchValue.trim()) {
                        url.searchParams.set('q', searchValue.trim());
                    } else {
                        url.searchParams.delete('q');
                    }
                    url.searchParams.set('page', '1');
                    window.history.pushState({}, '', url.toString());
                    
                    if (searchTimeout) {
                        clearTimeout(searchTimeout);
                    }
                    searchTimeout = setTimeout(performSearch, 500);
                };
                
                const handleSearchKeydown = (event) => {
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
                
                // Сохраняем все атрибуты и значение перед клонированием
                const currentValue = searchInput.value;
                const currentName = searchInput.getAttribute('name');
                const currentPlaceholder = searchInput.getAttribute('placeholder');
                const currentClass = searchInput.getAttribute('class');
                
                const newInput = searchInput.cloneNode(true);
                searchInput.parentNode.replaceChild(newInput, searchInput);
                const freshSearchInput = document.getElementById('global-search');
                
                if (!freshSearchInput) {
                    console.error('[VeilBot][subscriptions] Failed to find input after cloning');
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
                
                // Добавляем обработчики с оберткой для input, чтобы убедиться, что они срабатывают
                const inputHandler = (e) => {
                    console.log('[VeilBot][subscriptions] Input event triggered', e.target.value);
                    handleSearchInput(e);
                };
                
                freshSearchInput.addEventListener('input', inputHandler, { capture: true, passive: false });
                freshSearchInput.addEventListener('keydown', handleSearchKeydown, { capture: true, passive: false });
                searchForm.addEventListener('submit', handleSearchSubmit, { capture: true, passive: false });
                
                // Проверяем, что обработчик действительно добавлен
                console.log('[VeilBot][subscriptions] Event listeners added, input element:', freshSearchInput);
                
                const handleResetSearchUpdated = (event) => {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    
                    freshSearchInput.value = '';
                    
                    const url = new URL(window.location.href);
                    url.searchParams.delete('q');
                    url.searchParams.set('page', '1');
                    window.history.pushState({}, '', url.toString());
                    
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
                    resetSearchBtn.addEventListener('click', handleResetSearchUpdated, { capture: true, passive: false });
                }
                
                console.log('[VeilBot][subscriptions] Live search initialized on element:', freshSearchInput);
            } else {
                console.warn('[VeilBot][subscriptions] Search form or input not found');
            }
        }, 300); // Увеличена задержка до 300ms чтобы common.js успел проверить атрибуты

        updateProgressBars();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();







