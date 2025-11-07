import { showNotification, postForm } from '/static/js/common.js';

const reloadAfterDelay = (url) => {
    setTimeout(() => {
        if (url) {
            window.location.href = url;
        } else {
            window.location.reload();
        }
    }, 1000);
};

const handleSubmit = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;

    try {
        const response = await postForm(form.action, form);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Не удалось выполнить операцию');
        }
        showNotification(data.message || 'Операция выполнена успешно', 'success');
        reloadAfterDelay();
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
};

const handleDelete = async (event) => {
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
        reloadAfterDelay('/payments');
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
    return false;
};

const initPaymentDetailPage = () => {
    document.querySelectorAll('[data-payment-detail]').forEach((form) => {
        const action = form.dataset.paymentDetail;
        if (action === 'delete') {
            form.addEventListener('submit', handleDelete);
        } else {
            form.addEventListener('submit', handleSubmit);
        }
    });
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPaymentDetailPage);
} else {
    initPaymentDetailPage();
}

export {};
