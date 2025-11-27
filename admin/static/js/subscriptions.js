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
            console.log(`[VeilBot][subscriptions] Edit button trafficLimit attribute: ${editButton.dataset.trafficLimit}`);
        }

        // Обновляем информацию о трафике
        if (subscription.traffic) {
            const trafficDisplay = row.querySelector('[data-field="traffic-display"]');
            if (trafficDisplay && subscription.traffic.display) {
                trafficDisplay.textContent = subscription.traffic.display;
            }

            const trafficLimit = row.querySelector('[data-field="traffic-limit"]');
            if (trafficLimit) {
                if (subscription.traffic.limit_display && subscription.traffic.limit_display !== '—') {
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

    const handleSyncKeys = async () => {
        const confirmed = window.confirm('Запустить синхронизацию ключей с серверами? Это может занять некоторое время.');
        if (!confirmed) {
            return;
        }

        const syncButton = document.getElementById('sync-keys-btn');
        if (syncButton) {
            syncButton.disabled = true;
            const originalText = syncButton.innerHTML;
            syncButton.innerHTML = '<span class="material-icons icon-small">hourglass_empty</span> Синхронизация...';
            
            try {
                // Создаем AbortController для таймаута (5 минут)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 5 * 60 * 1000);
                
                const response = await fetch('/subscriptions/sync-keys', {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
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
                
                // Показываем уведомление с увеличенным временем отображения
                notify(message, 'success', 5000);
                
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
                    errorMessage = 'Синхронизация прервана по таймауту (более 5 минут). Процесс может продолжаться на сервере.';
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
            syncButton.addEventListener('click', handleSyncKeys);
        }

        if (window.VeilBotCommon && typeof window.VeilBotCommon.initTableSearch === 'function') {
            window.VeilBotCommon.initTableSearch({
                tableSelector: '#subscriptions-table',
            });
        } else {
            console.warn('[VeilBot][subscriptions] initTableSearch недоступен');
        }

        updateProgressBars();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();







