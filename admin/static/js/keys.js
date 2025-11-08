import {
    ModalController,
    showNotification,
    handleError,
    updateProgressBars,
} from '/static/js/common.js';

const state = {
    modal: null,
    editForm: null,
    expiryInput: null,
    currentKeyId: null,
    trafficLimitInput: null,
};

const copyToClipboard = async (value) => {
    if (!value) {
        return;
    }

    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(value);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = value;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
        showNotification('Ключ скопирован!', 'success');
    } catch (error) {
        handleError(error, 'Не удалось скопировать ключ');
    }
};

const updateStats = (stats) => {
    if (!stats) return;
    Object.entries(stats).forEach(([key, value]) => {
        const card = document.querySelector(`.stat-card[data-stat="${key}"] [data-stat-value]`);
        if (card) {
            card.textContent = value;
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
        expiryBar.dataset.progress = Math.round((key.lifetime_progress || 0) * 100);
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
        const limitMb = key.traffic && key.traffic.limit_mb != null ? String(key.traffic.limit_mb) : '';
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
            limit.textContent = key.traffic.limit_display && key.traffic.limit_display !== '—'
                ? `Лимит: ${key.traffic.limit_display}`
                : 'Лимит не задан';
            if (!key.traffic.limit_display || key.traffic.limit_display === '—') {
                limit.classList.add('text-muted');
            } else {
                limit.classList.remove('text-muted');
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
            trafficBar.dataset.progress = percent;
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

        showNotification(data.message || 'Срок действия обновлён', 'success');
        if (state.modal) {
            state.modal.close();
        }
    } catch (error) {
        handleError(error, 'Обновление срока действия');
    } finally {
        resetFormLoadingState(form);
    }
};

const openEditModal = (trigger) => {
    if (!state.modal || !state.editForm || !state.expiryInput) {
        return;
    }
    const keyId = trigger.dataset.keyId;
    if (!keyId) {
        return;
    }
    const expiryValue = trigger.dataset.expiry || '';
    state.currentKeyId = keyId;
    state.editForm.dataset.keyId = keyId;
    state.editForm.action = `/keys/edit/${keyId}`;
    state.expiryInput.value = expiryValue;
    if (state.trafficLimitInput) {
        const limitValue = trigger.dataset.limit ?? '';
        state.trafficLimitInput.value = limitValue !== undefined ? limitValue : '';
    }
    state.modal.open();
    state.expiryInput.focus();
};

const handleDeleteKey = async (trigger) => {
    const keyId = trigger.dataset.keyId;
    if (!keyId) {
        return;
    }

    const confirmed = window.confirm(`Удалить ключ ${keyId}?`);
    if (!confirmed) {
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
        showNotification(data.message || 'Ключ удалён', 'success');
    } catch (error) {
        handleError(error, 'Удаление ключа');
        if (fallbackUrl) {
            window.location.href = fallbackUrl;
        }
    }
};

const handleTableClick = (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;

    const action = trigger.dataset.action;
    if (action === 'copy-key') {
        event.preventDefault();
        copyToClipboard(trigger.dataset.key || '');
    }

    if (action === 'edit-key') {
        event.preventDefault();
        openEditModal(trigger);
    }

    if (action === 'delete-key') {
        event.preventDefault();
        handleDeleteKey(trigger);
    }
};

const initKeysPage = () => {
    const modalElement = document.getElementById('edit-key-modal');
    if (modalElement) {
        state.modal = new ModalController(modalElement);
        state.modal.close();
    }

    state.editForm = document.getElementById('editForm');
    state.expiryInput = document.getElementById('new_expiry');
    state.trafficLimitInput = document.getElementById('traffic_limit_mb');

    if (state.editForm) {
        state.editForm.addEventListener('submit', handleEditSubmit);
    }

    const table = document.getElementById('keys-table');
    if (table) {
        table.addEventListener('click', handleTableClick);
    }

    updateProgressBars();
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initKeysPage);
} else {
    initKeysPage();
}

export {};