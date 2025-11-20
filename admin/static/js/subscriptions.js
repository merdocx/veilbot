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
            return;
        }
        const subscriptionId = trigger.dataset.subscriptionId;
        if (!subscriptionId) {
            return;
        }
        const expiryValue = trigger.dataset.expiry || '';

        state.currentSubscriptionId = subscriptionId;
        state.editForm.dataset.subscriptionId = subscriptionId;
        state.editForm.action = `/subscriptions/edit/${subscriptionId}`;
        state.expiryInput.value = expiryValue;

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
        if (!subscription) return;
        const row = document.querySelector(`.subscriptions-table__row[data-subscription-id="${subscription.id}"]`);
        if (!row) return;

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
                throw new Error(data.error || 'Не удалось обновить подписку');
            }

            if (data.subscription) {
                applySubscriptionUpdate(data.subscription);
            }
            if (data.stats) {
                updateStats(data.stats);
            }

            notify(data.message || 'Параметры подписки обновлены', 'success');
            closeModal();
        } catch (error) {
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







