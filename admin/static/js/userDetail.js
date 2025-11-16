import { showNotification, postForm } from '/static/js/common.js';

const copyToClipboard = async (value) => {
    const text = value || '';
    if (!text) {
        showNotification('Ключ отсутствует', 'warning');
        return false;
    }

    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(text);
            showNotification('Ключ скопирован в буфер обмена', 'success');
            return true;
        }
    } catch (error) {
        console.error('Clipboard API error:', error);
    }

    try {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        textarea.style.pointerEvents = 'none';
        document.body.appendChild(textarea);
        textarea.select();
        const success = document.execCommand('copy');
        document.body.removeChild(textarea);
        if (success) {
            showNotification('Ключ скопирован в буфер обмена', 'success');
            return true;
        }
    } catch (error) {
        console.error('Fallback copy error:', error);
    }

    showNotification('Скопируйте ключ вручную (Ctrl/Cmd + C)', 'info');
    return false;
};

const handleCopyKey = async (event) => {
    event.preventDefault();
    const button = event.currentTarget;
    const key = button.dataset.key;
    if (key) {
        await copyToClipboard(key);
    }
};

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
    document.querySelectorAll('[data-action="copy-key"]').forEach((button) => {
        button.addEventListener('click', handleCopyKey);
    });

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
