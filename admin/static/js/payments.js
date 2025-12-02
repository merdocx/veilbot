import { showNotification, postForm } from '/static/js/common.js';

const handleAjaxForm = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;

    try {
        const response = await postForm(form.action, form);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Не удалось выполнить операцию');
        }
        showNotification(data.message || 'Операция выполнена успешно', 'success');
        setTimeout(() => window.location.reload(), 1000);
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
};

const handleDeletePayment = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const paymentId = form.dataset.paymentId || '';

    const confirmed = window.confirm(
        paymentId
            ? `Вы уверены, что хотите удалить платеж ${paymentId}? Это действие нельзя отменить.`
            : 'Вы уверены, что хотите удалить платеж?'
    );
    if (!confirmed) {
        return false;
    }

    try {
        const response = await postForm(form.action, form);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Не удалось удалить платеж');
        }
        showNotification(data.message || 'Платеж удален', 'success');
        setTimeout(() => window.location.reload(), 1000);
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
    return false;
};

const handleReconcile = async (csrfToken) => {
    const confirmed = window.confirm('Запустить реконсиляцию платежей?');
    if (!confirmed) {
        return;
    }

    try {
        const response = await fetch('/payments/reconcile', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: `csrf_token=${encodeURIComponent(csrfToken)}`,
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Ошибка реконсиляции');
        }
        showNotification(`Реконсиляция завершена. Обработано: ${data.processed}`, 'success');
        setTimeout(() => window.location.reload(), 1500);
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
};

const initStatusFilters = () => {
    const statusButtons = document.querySelectorAll('[data-status-filter]');
    const filterForm = document.querySelector('[data-payments-filter-form]');
    const statusSelect = document.querySelector('[data-filter-status-select]');
    const presetInput = document.querySelector('[data-filter-preset-input]');

    if (statusSelect && presetInput) {
        statusSelect.addEventListener('change', () => {
            if (presetInput.value) {
                presetInput.value = '';
            }
        });
    }

    if (!statusButtons.length || !filterForm || !statusSelect) {
        return;
    }

    statusButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const statusValue = button.dataset.statusFilter || '';
            const presetValue = button.dataset.presetFilter || '';

            if (statusSelect.value !== statusValue) {
                statusSelect.value = statusValue;
            }
            if (presetInput && presetInput.value !== presetValue) {
                presetInput.value = presetValue;
            }

            statusButtons.forEach((btn) => {
                btn.setAttribute('aria-pressed', btn === button ? 'true' : 'false');
            });

            if (typeof filterForm.requestSubmit === 'function') {
                filterForm.requestSubmit();
            } else {
                filterForm.submit();
            }
        });
    });
};

const initPaymentsPage = () => {
    document.querySelectorAll('[data-action="payments-form"]').forEach((form) => {
        form.addEventListener('submit', handleAjaxForm);
    });

    document.querySelectorAll('[data-action="delete-payment"]').forEach((form) => {
        form.addEventListener('submit', handleDeletePayment);
    });

    document.querySelectorAll('[data-action="reconcile"]').forEach((button) => {
        button.addEventListener('click', () => {
            const csrfToken = button.dataset.csrf;
            if (csrfToken) {
                handleReconcile(csrfToken);
            }
        });
    });

    initStatusFilters();

    // Инициализируем live-поиск
    if (typeof window.initLiveSearch === 'function') {
        window.initLiveSearch({
            pageUrl: '/payments',
            tableSelector: '#payments-table',
            statsSelector: '.stats-grid',
            paginationSelector: '.pagination',
        });
    } else {
        console.warn('[VeilBot][payments] initLiveSearch недоступен');
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPaymentsPage);
} else {
    initPaymentsPage();
}

export {};
