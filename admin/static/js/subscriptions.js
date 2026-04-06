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
            const iconName = subscription.status === "Активна" ? "check_circle" : (subscription.status === "Неактивна" ? "remove_circle" : "cancel");
            statusIcon.textContent = iconName;
            statusIcon.setAttribute('title', subscription.status || '');
            statusIcon.classList.remove('status-active', 'status-expired', 'status-inactive');
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
        console.log('[VeilBot][subscriptions] ===== openSyncKeysModal called =====');
        const syncModal = document.getElementById('sync-keys-modal');
        if (!syncModal) {
            console.error('[VeilBot][subscriptions] ❌ Sync keys modal not found in DOM');
            const allModals = document.querySelectorAll('.vb-modal');
            console.error('[VeilBot][subscriptions] Available modals:', allModals.length);
            allModals.forEach((modal, idx) => {
                console.error(`[VeilBot][subscriptions] Modal ${idx}: id="${modal.id}", classes="${modal.className}"`);
            });
            notify('Модальное окно синхронизации не найдено. Перезагрузите страницу.', 'error', 5000);
            return;
        }
        console.log('[VeilBot][subscriptions] ✅ Found sync modal, opening...');
        console.log('[VeilBot][subscriptions] Modal classes before:', syncModal.className);
        console.log('[VeilBot][subscriptions] Modal style.display before:', window.getComputedStyle(syncModal).display);
        
        syncModal.classList.add('vb-modal--open');
        syncModal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('vb-modal-open');
        
        console.log('[VeilBot][subscriptions] Modal classes after:', syncModal.className);
        console.log('[VeilBot][subscriptions] Modal style.display after:', window.getComputedStyle(syncModal).display);
        console.log('[VeilBot][subscriptions] Body classes:', document.body.className);
        
        // Принудительно устанавливаем display: flex на случай проблем с CSS
        const computedDisplay = window.getComputedStyle(syncModal).display;
        if (computedDisplay === 'none') {
            console.warn('[VeilBot][subscriptions] ⚠️ Modal still has display:none, forcing display:flex');
            syncModal.style.display = 'flex';
        }
        
        const firstFocusable = syncModal.querySelector('[data-modal-focus], input, button, select, textarea');
        if (firstFocusable && typeof firstFocusable.focus === 'function') {
            firstFocusable.focus();
        }
        console.log('[VeilBot][subscriptions] ===== Modal should be visible now =====');
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
            const dryRunFalse = syncForm.querySelector('input[name="dry_run"][value="false"]');
            if (dryRunFalse) {
                dryRunFalse.checked = true; // По умолчанию выбран "Реальная синхронизация"
            }
            const serverScopeAll = syncForm.querySelector('input[name="server_scope"][value="all"]');
            if (serverScopeAll) {
                serverScopeAll.checked = true;
            }
            syncForm.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
            const serverSelectField = document.getElementById('server-select-field');
            if (serverSelectField) {
                serverSelectField.style.display = 'none';
            }
        }
    };
    
    // Привязываем обработчики закрытия модального окна синхронизации
    const bindSyncModalHandlers = () => {
        const syncModal = document.getElementById('sync-keys-modal');
        if (!syncModal) {
            console.warn('[VeilBot][subscriptions] Sync modal not found for binding handlers');
            return;
        }
        
        // Обработчик закрытия по клику на backdrop
        const backdrop = syncModal.querySelector('.vb-modal__backdrop');
        if (backdrop) {
            backdrop.addEventListener('click', closeSyncKeysModal);
        }
        
        // Обработчик закрытия по кнопкам с data-modal-close
        syncModal.querySelectorAll('[data-modal-close]').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.preventDefault();
                closeSyncKeysModal();
            });
        });
        
        // Обработчик закрытия по Escape
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && syncModal.classList.contains('vb-modal--open')) {
                closeSyncKeysModal();
            }
        });
        
        console.log('[VeilBot][subscriptions] Sync modal handlers bound');
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
                const serversProcessed = stats.servers_processed || 0;
                const serversUnavailable = stats.servers_unavailable || 0;
                const keysDeletedDb = stats.keys_deleted_from_db || 0;
                const keysDeletedRemote = stats.keys_deleted_from_servers || 0;
                const v2rayCreated = stats.v2ray_keys_created || 0;
                const v2rayConfigsUpdated = stats.v2ray_configs_updated || 0;
                const errorCount = stats.errors || 0;
                const durationSec = stats.duration_seconds != null ? Number(stats.duration_seconds) : null;
                const errorDetails = stats.error_details || [];
                const dryRun = data.dry_run === true;

                let message = dryRun
                    ? '🔍 Режим проверки (dry run): изменения на серверах и в БД не применялись.\n\n'
                    : '';
                message += '✅ Синхронизация ключей завершена.\n';
                message += `Создано ключей: ${v2rayCreated}, обновлено VLESS-конфигов: ${v2rayConfigsUpdated}.\n`;
                message += `Удалено из БД: ${keysDeletedDb}, с серверов (API): ${keysDeletedRemote}.\n`;
                message += `Серверов в этапе создания (обработано): ${serversProcessed}`;
                if (serversUnavailable > 0) {
                    message += `, недоступных по БД (inactive): ${serversUnavailable}`;
                }
                if (durationSec != null && !Number.isNaN(durationSec)) {
                    message += `.\nВремя: ${durationSec.toFixed(1)} с`;
                }
                if (errorCount > 0) {
                    message += `.\n⚠️ Ошибок при обработке: ${errorCount}`;
                }

                if (errorDetails.length > 0) {
                    message += '\n\nДетали (до 10):\n';
                    errorDetails.slice(0, 10).forEach((err, index) => {
                        const typeInfo = err.type ? `[${err.type}] ` : '';
                        const sid = err.server_id != null ? `сервер #${err.server_id}` : '';
                        const sub = err.subscription_id != null ? `подписка #${err.subscription_id}` : '';
                        const scope = [sid, sub].filter(Boolean).join(', ');
                        const text = err.error || err.message || JSON.stringify(err);
                        message += `${index + 1}. ${typeInfo}${scope ? `${scope}: ` : ''}${text}\n`;
                    });
                    if (errorDetails.length > 10) {
                        message += `… и ещё ${errorDetails.length - 10} записей (см. логи сервера).`;
                    }
                }

                const notificationTime = errorCount > 0 ? 15000 : 6000;
                notify(message, errorCount > 0 ? 'warning' : 'success', notificationTime);
                
                // Восстанавливаем кнопку с индикацией успеха
                syncButton.innerHTML = '<span class="material-icons icon-small">check_circle</span> Синхронизировано';
                syncButton.classList.add('btn-success');
                
                const reloadDelay = (errorCount > 0 || errorDetails.length > 0) ? 8500 : 4000;
                setTimeout(() => {
                    window.location.reload();
                }, reloadDelay);
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
            include_v2ray: true,
        };
        
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

    const copyToClipboard = async (value) => {
        const text = value || '';
        if (!text) {
            notify('Ссылка подписки отсутствует', 'warning');
            return false;
        }

        try {
            if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(text);
                notify('Ссылка подписки скопирована в буфер обмена', 'success');
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
                notify('Ссылка подписки скопирована в буфер обмена', 'success');
                return true;
            }
        } catch (error) {
            handleError(error, 'document.execCommand copy');
        }

        window.prompt('Скопируйте ссылку подписки вручную (Ctrl/Cmd + C):', text);
        return false;
    };

    const handleTableClick = (event) => {
        // Игнорируем клики по кнопке синхронизации и другим элементам вне таблицы
        if (event.target.closest('#sync-keys-btn') || 
            event.target.closest('.filter-bar') ||
            event.target.closest('.card-header')) {
            return;
        }
        
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
            return;
        }

        if (action === 'copy-subscription') {
            event.preventDefault();
            const subscriptionUrl = trigger.dataset.subscriptionUrl;
            if (subscriptionUrl) {
                copyToClipboard(subscriptionUrl);
            } else {
                notify('Ссылка подписки отсутствует', 'warning');
            }
        }
        
        // Игнорируем другие действия, которые не обрабатываются здесь
        if (action === 'sync-keys') {
            // Это обрабатывается отдельным обработчиком на кнопке
            return;
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

        // Инициализация кнопки синхронизации
        const initSyncButton = () => {
            const syncButton = document.getElementById('sync-keys-btn');
            if (syncButton) {
                console.log('[VeilBot][subscriptions] Found sync button, adding event listener');
                // Удаляем старый обработчик, если есть
                syncButton.removeEventListener('click', openSyncKeysModal);
                // Добавляем новый обработчик
                syncButton.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('[VeilBot][subscriptions] Sync button clicked');
                    openSyncKeysModal();
                });
                console.log('[VeilBot][subscriptions] Sync button event listener added');
            } else {
                console.error('[VeilBot][subscriptions] Sync button not found!');
            }
        };
        
        initSyncButton();
        
        // Проверяем наличие модального окна синхронизации
        const syncModalCheck = document.getElementById('sync-keys-modal');
        if (syncModalCheck) {
            console.log('[VeilBot][subscriptions] ✅ Sync modal found in DOM');
            // Привязываем обработчики модального окна синхронизации
            bindSyncModalHandlers();
        } else {
            console.error('[VeilBot][subscriptions] ❌ Sync modal NOT found in DOM at init time!');
            console.error('[VeilBot][subscriptions] This may cause the button to not work.');
            // Попробуем найти его позже
            setTimeout(() => {
                const retryModal = document.getElementById('sync-keys-modal');
                if (retryModal) {
                    console.log('[VeilBot][subscriptions] ✅ Sync modal found on retry, binding handlers');
                    bindSyncModalHandlers();
                } else {
                    console.error('[VeilBot][subscriptions] ❌ Sync modal still not found after retry');
                }
            }, 100);
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
        if (typeof window.initLiveSearch === 'function') {
            window.initLiveSearch({
                pageUrl: '/subscriptions',
                tableSelector: '#subscriptions-table',
                statsSelector: '.stats-grid',
                paginationSelector: '.pagination',
            });
        } else {
            console.warn('[VeilBot][subscriptions] initLiveSearch недоступен, загружаем скрипт...');
            const script = document.createElement('script');
            script.src = '/static/js/live-search.js';
            script.onload = () => {
                window.initLiveSearch({
                    pageUrl: '/subscriptions',
                    tableSelector: '#subscriptions-table',
                    statsSelector: '.stats-grid',
                    paginationSelector: '.pagination',
                });
            };
            document.head.appendChild(script);
        }

        updateProgressBars();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();







