import { showNotification, postForm } from '/static/js/common.js';

const handleKeyResend = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
        const response = await postForm(form.action, form);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Не удалось отправить ключ повторно');
        }
        showNotification(data.message || 'Ключ отправлен пользователю', 'success');
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
};

const handleExtend = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
        const response = await postForm(form.action, form);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Не удалось продлить ключ');
        }
        showNotification(data.message || 'Срок действия продлён', 'success');
        setTimeout(() => window.location.reload(), 1000);
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
};

const handlePaymentAction = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
        const response = await postForm(form.action, form);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Не удалось выполнить действие');
        }
        showNotification(data.message || 'Операция выполнена', 'success');
        setTimeout(() => window.location.reload(), 1000);
    } catch (error) {
        showNotification(error.message || String(error), 'error');
    }
};

const initUserDetailPage = () => {
    document.querySelectorAll('[data-user-action="resend"]').forEach((form) => {
        form.addEventListener('submit', handleKeyResend);
    });

    document.querySelectorAll('[data-user-action="extend"]').forEach((form) => {
        form.addEventListener('submit', handleExtend);
    });

    document.querySelectorAll('[data-user-payment]').forEach((form) => {
        form.addEventListener('submit', handlePaymentAction);
    });
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUserDetailPage);
} else {
    initUserDetailPage();
}

export {};
